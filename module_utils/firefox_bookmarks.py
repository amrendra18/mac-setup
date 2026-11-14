import base64
import ctypes
import errno
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import stat
import subprocess
import time
import tempfile

try:
    from ansible.module_utils.browser_settings import _is_browser
except ImportError:
    import importlib.util

    reader_spec = importlib.util.spec_from_file_location(
        "bookmarks_browser_reader", Path(__file__).with_name("browser_settings.py")
    )
    reader = importlib.util.module_from_spec(reader_spec)
    reader_spec.loader.exec_module(reader)
    _is_browser = reader._is_browser

FIREFOX = "/Applications/Firefox.app/Contents/MacOS/firefox"
MAX_SOURCE = 16 * 1024 * 1024


class BookmarkError(Exception):
    pass


class PermissionDenied(BookmarkError):
    pass


class StartupError(BookmarkError):
    def __init__(self, message, check="automation"):
        super().__init__(message)
        self.check = check


PERMISSION_MESSAGE = (
    "macOS denied data access. Run mac-setup at the Mac's screen. "
    "Grant Full Disk Access or Firefox App Data access to the terminal app."
)
SYSTEM_ACCESS_MESSAGE = (
    "Firefox chrome context and system access check failed. "
    "Firefox refused privileged bookmark automation. "
    "Check the installed Firefox version and Firefox developer tools policies."
)
ISOLATION_MESSAGE = (
    "Firefox profile isolation check failed. "
    "Firefox did not confirm the requested verification target."
)
STATE_DIRECTORY_MESSAGE = (
    "Firefox bookmark state directory check failed. "
    "State and verification directories must belong to this account, "
    "have mode 0700, and contain no symbolic links."
)


def observed_version(session):
    capabilities = session.get("capabilities") if isinstance(session, dict) else None
    version = (
        capabilities.get("browserVersion") if isinstance(capabilities, dict) else None
    )
    if isinstance(version, str) and re.fullmatch(
        r"[0-9]{1,4}(?:\.[0-9]{1,4}){0,3}", version
    ):
        return version
    return "unknown"


def require(condition, message):
    if not condition:
        raise BookmarkError(message)


def safe_path(path):
    path = Path(path).absolute()
    for prefix in ("/var", "/tmp"):
        if path == Path(prefix) or Path(prefix) in path.parents:
            path = Path("/private") / path.relative_to("/")
    for parent in [path, *path.parents]:
        require(not parent.is_symlink(), "Firefox import refuses symbolic links.")
    return path


def private_path(path, directory=False):
    path = safe_path(path)
    info = path.stat()
    require(
        info.st_uid == os.getuid()
        and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)),
        "Firefox import needs files owned by the target account.",
    )
    return path


class BookmarksHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots = {name: [] for name in ("menu", "toolbar", "unfiled")}
        self.stack = []
        self.current = self.roots["menu"]
        self.pending = None
        self.capture = None
        self.text = []
        self.attrs = {}
        self.count = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("h3", "a"):
            require(self.capture is None, "Malformed bookmarks HTML.")
            self.capture, self.attrs, self.text = tag, attrs, []
        elif tag == "dl":
            self.stack.append(self.current)
            if self.pending is not None:
                self.current, self.pending = self.pending, None
            require(
                len(self.stack) < 100, "Bookmarks HTML folders are too deeply nested."
            )
        elif tag == "hr":
            self.current.append({"type": 3})

    def handle_data(self, data):
        if self.capture:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag == "dl":
            require(bool(self.stack), "Malformed bookmarks HTML folders.")
            self.current = self.stack.pop()
        elif tag == self.capture:
            title = "".join(self.text)
            if tag == "h3":
                special = next(
                    (
                        root
                        for field, root in (
                            ("personal_toolbar_folder", "toolbar"),
                            ("unfiled_bookmarks_folder", "unfiled"),
                        )
                        if self.attrs.get(field, "").lower() == "true"
                    ),
                    None,
                )
                if special:
                    self.pending = self.roots[special]
                else:
                    folder = {"type": 2, "title": title, "children": []}
                    self.current.append(folder)
                    self.pending = folder["children"]
            else:
                url = self.attrs.get("href", "")
                require(
                    bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url))
                    and not any(ord(char) < 32 for char in url),
                    "Bookmarks HTML contains an invalid address.",
                )
                self.current.append({"type": 1, "title": title, "url": url})
                self.count += 1
                require(self.count <= 20000, "Bookmarks HTML exceeds 20000 bookmarks.")
            self.capture = None


def parse_html(source):
    require(
        len(source.encode("utf-8")) <= MAX_SOURCE
        and "<!doctype netscape-bookmark-file-1>" in source.lower(),
        "Use a Netscape bookmarks HTML export smaller than 16 MiB.",
    )
    parser = BookmarksHTML()
    try:
        parser.feed(source)
        parser.close()
        require(
            parser.count > 0 and not parser.stack and parser.capture is None,
            "Bookmarks HTML is empty or incomplete.",
        )
    except (ValueError, RecursionError):
        raise BookmarkError("Could not parse bookmarks HTML.") from None

    def identify(nodes, parent):
        occurrences = {}
        for node in nodes:
            identity = json.dumps(
                [node["type"], node.get("title", ""), node.get("url", "")],
                ensure_ascii=False,
            )
            ordinal = occurrences.get(identity, 0)
            occurrences[identity] = ordinal + 1
            digest = hashlib.sha256(
                json.dumps([parent, identity, ordinal]).encode("utf-8")
            ).digest()
            node["guid"] = base64.urlsafe_b64encode(digest[:9]).decode("ascii")
            if node["type"] == 2:
                identify(node["children"], node["guid"])

    for name, nodes in parser.roots.items():
        identify(nodes, "mac-setup-bookmarks-v1-" + name)
    return parser.roots


def check_closed():
    result = subprocess.run(
        ["/bin/ps", "-axo", "comm="], capture_output=True, text=True, timeout=15
    )
    require(result.returncode == 0, "Could not check whether Firefox is closed.")
    require(
        not any(
            _is_browser("firefox", command.strip())
            for command in result.stdout.splitlines()
        ),
        "Quit Firefox in every account before importing bookmarks.",
    )


def receive(stream):
    header = bytearray()
    while True:
        char = stream.read(1)
        require(bool(char), "Firefox closed the import connection.")
        if char == b":":
            break
        require(char in b"0123456789" and len(header) < 8, "Invalid Firefox response.")
        header.extend(char)
    require(bool(header), "Invalid Firefox response.")
    size = int(header)
    require(0 < size <= 2 * MAX_SOURCE, "Firefox response exceeds the import limit.")
    payload = bytearray()
    while len(payload) < size:
        block = stream.read(size - len(payload))
        require(bool(block), "Firefox closed the import connection.")
        payload.extend(block)
    return json.loads(payload)


IMPORT_SCRIPT = r"""
const [roots, done] = arguments;
(async () => {
  const { PlacesUtils } = ChromeUtils.importESModule(
    'resource://gre/modules/PlacesUtils.sys.mjs');
  const bookmarks = PlacesUtils.bookmarks;
  let imported = 0;
  async function insert(nodes, parentGuid) {
    for (const node of nodes) {
      let existing = await bookmarks.fetch(node.guid);
      if (existing) {
        if (existing.type !== node.type) throw new Error('Import identity conflict');
      } else {
        const item = {guid: node.guid, parentGuid, type: node.type};
        if (node.type !== bookmarks.TYPE_SEPARATOR) item.title = node.title;
        if (node.type === bookmarks.TYPE_BOOKMARK) item.url = node.url;
        existing = await bookmarks.insert(item);
        if (node.type === bookmarks.TYPE_BOOKMARK) imported++;
      }
      if (node.type === bookmarks.TYPE_FOLDER) {
        await insert(node.children, existing.guid);
      }
    }
  }
  await insert(roots.menu, bookmarks.menuGuid);
  await insert(roots.toolbar, bookmarks.toolbarGuid);
  await insert(roots.unfiled, bookmarks.unfiledGuid);
  done({imported});
})().catch(() => done({error: true}));
"""


IDENTITY_SCRIPT = r"""
const [root] = arguments;
const path = Services.dirsvc.get('ProfD', Ci.nsIFile).path;
const isolated = !root || path.startsWith(root + '/');
if (!isolated) return {isolated: false};
const pref = 'mac_setup.bookmarks.profile_id';
let profileId = Services.prefs.getStringPref(pref, '');
let created = false;
if (!profileId) {
  profileId = Services.uuid.generateUUID().toString().replace(/[{}]/g, '');
  Services.prefs.setStringPref(pref, profileId);
  Services.prefs.savePrefFile(null);
  created = true;
}
return {isolated: true, profile_id: profileId, identity_created: created};
"""


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    require(
        type(port) is int and 0 < port < 65536, "Could not select a local Firefox port."
    )
    return port


GUI_MESSAGE = (
    "Firefox bookmark import needs this account's graphical login session. "
    "Sign in to this account at the Mac's screen and retry."
)
LAUNCH_MESSAGE = (
    "LaunchServices could not start Firefox for bookmark automation. "
    "Keep this account signed in at the Mac's screen, quit Firefox, and retry."
)


def require_gui_session():
    result = subprocess.run(
        ["/bin/launchctl", "print", f"gui/{os.getuid()}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    if result.returncode != 0:
        raise StartupError(GUI_MESSAGE, check="gui")


def process_snapshot():
    result = subprocess.run(
        ["/bin/ps", "-ww", "-axo", "pid=,uid=,lstart=,stat=,command="],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=os.environ | {"LC_ALL": "C"},
        timeout=15,
    )
    require(result.returncode == 0, "Could not identify the launched Firefox process.")
    records = {}
    for row in result.stdout.splitlines():
        fields = row.split(None, 8)
        if fields and fields[0].isdigit() and int(fields[0]) > 1:
            records[int(fields[0])] = None
        if (
            len(fields) == 9
            and fields[0].isdigit()
            and int(fields[0]) > 1
            and fields[1].isdigit()
        ):
            records[int(fields[0])] = (
                int(fields[1]),
                tuple(fields[2:7]),
                fields[7],
                fields[8],
            )
    return records


def process_executable(pid):
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lookup = library.proc_pidpath
        lookup.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        lookup.restype = ctypes.c_int
        buffer = ctypes.create_string_buffer(4096)
        ctypes.set_errno(0)
        size = lookup(pid, buffer, len(buffer))
    except OSError:
        raise BookmarkError(
            "Could not verify the launched Firefox executable."
        ) from None
    if size <= 0 and ctypes.get_errno() == errno.ESRCH:
        return None
    require(0 < size < len(buffer), "Could not verify the launched Firefox executable.")
    return os.fsdecode(buffer.value)


class ProcessBSDInfo(ctypes.Structure):
    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("exit_status", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("parent_pid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("real_uid", ctypes.c_uint32),
        ("real_gid", ctypes.c_uint32),
        ("saved_uid", ctypes.c_uint32),
        ("saved_gid", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("command", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("file_count", ctypes.c_uint32),
        ("process_group", ctypes.c_uint32),
        ("job_count", ctypes.c_uint32),
        ("terminal_device", ctypes.c_uint32),
        ("terminal_group", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("seconds", ctypes.c_uint64),
        ("microseconds", ctypes.c_uint64),
    ]


def process_birth(pid):
    try:
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        lookup = library.proc_pidinfo
        lookup.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_int,
        ]
        lookup.restype = ctypes.c_int
        info = ProcessBSDInfo()
        ctypes.set_errno(0)
        size = lookup(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    except OSError:
        raise BookmarkError(
            "Could not verify the launched Firefox birth identity."
        ) from None
    if size <= 0 and ctypes.get_errno() == errno.ESRCH:
        return None
    require(
        size == ctypes.sizeof(info)
        and info.pid == pid
        and info.seconds > 0
        and info.microseconds < 1000000,
        "Could not verify the launched Firefox birth identity.",
    )
    return info.uid, info.seconds, info.microseconds


class FirefoxProcess:
    def __init__(self, pid, identity):
        self.pid = pid
        self.identity = identity

    @staticmethod
    def matches(record, token):
        if record is None:
            return False
        uid, started, state, arguments = record
        command = arguments.split()
        return (
            uid == os.getuid()
            and bool(command)
            and command[0] == FIREFOX
            and token in command[1:]
            and not state.startswith("Z")
        )

    @classmethod
    def find(cls, token):
        matches = []
        for pid, record in process_snapshot().items():
            if not cls.matches(record, token):
                continue
            birth = process_birth(pid)
            if birth is None or birth[0] != os.getuid():
                continue
            current = process_snapshot().get(pid)
            if (
                cls.matches(current, token)
                and process_executable(pid) == FIREFOX
                and process_birth(pid) == birth
            ):
                matches.append(cls(pid, birth))
        require(len(matches) <= 1, "Firefox startup matched more than one process.")
        return matches[0] if matches else None

    def poll(self):
        records = process_snapshot()
        if self.pid not in records:
            return 1
        record = records[self.pid]
        require(
            record is not None, "Could not verify the launched Firefox process exit."
        )
        if record[2].startswith("Z"):
            return 1
        if record[0] != self.identity[0] or process_birth(self.pid) != self.identity:
            return 1
        executable = process_executable(self.pid)
        if executable is None:
            return 1
        require(
            executable == FIREFOX,
            "Firefox executable changed before shutdown. Quit Firefox and retry.",
        )
        return None

    def wait(self, timeout):
        deadline = time.monotonic() + timeout
        while self.poll() is None:
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired("Firefox", timeout)
            time.sleep(0.1)

    def stop(self, number):
        if self.poll() is None:
            try:
                os.kill(self.pid, number)
            except ProcessLookupError:
                pass

    def terminate(self):
        self.stop(signal.SIGTERM)

    def kill(self):
        self.stop(signal.SIGKILL)


def tcp_endpoint(value):
    address, port = value.rsplit(":", 1)
    address = ipaddress.ip_address(address.strip("[]"))
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return str(address), int(port)


def verify_peer(connection, process, port, timeout=15):
    message = "Could not verify that Firefox owns the automation connection."
    try:
        local = connection.getsockname()
        remote = connection.getpeername()
        require(
            remote == ("127.0.0.1", port)
            and local[0] == "127.0.0.1"
            and type(local[1]) is int
            and 0 < local[1] < 65536
            and process.poll() is None,
            message,
        )
        result = subprocess.run(
            [
                "/usr/sbin/lsof",
                "-n",
                "-P",
                "-a",
                "-p",
                str(process.pid),
                f"-iTCP@127.0.0.1:{port}",
                "-sTCP:ESTABLISHED",
                "-Fpn",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
        )
        require(result.returncode == 0, message)
        owner = None
        matched = False
        for row in result.stdout.splitlines():
            if row.startswith("p"):
                owner = row[1:]
            elif row.startswith("n") and owner == str(process.pid):
                addresses = row[1:].split("->")
                if len(addresses) != 2:
                    continue
                try:
                    matched |= (
                        tcp_endpoint(addresses[0]) == remote
                        and tcp_endpoint(addresses[1]) == local
                    )
                except ValueError:
                    continue
        require(matched and process.poll() is None, message)
    except (OSError, ValueError, subprocess.SubprocessError):
        raise BookmarkError(message) from None


def wait_for_peer(connection, process, port, deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StartupError(
                "Could not verify that Firefox owns the automation connection."
            )
        try:
            verify_peer(connection, process, port, timeout=min(15, remaining))
            return
        except BookmarkError:
            time.sleep(min(0.05, remaining))


class FirefoxSession:
    def __init__(self, firefox_root=None):
        self.root = safe_path(firefox_root) if firefox_root is not None else None
        self.process = None
        self.launcher = None
        self.launch_token = None
        self.connection = None
        self.stream = None
        self.sequence = 0
        self.verified = False
        self.profile_key = None
        self.identity_created = False
        self.port = None
        self.version = "unknown"

    def send(self, command, parameters):
        self.sequence += 1
        payload = json.dumps([0, self.sequence, command, parameters]).encode("utf-8")
        self.connection.sendall(str(len(payload)).encode("ascii") + b":" + payload)
        response = receive(self.stream)
        require(
            isinstance(response, list)
            and len(response) == 4
            and response[:2] == [1, self.sequence],
            "Firefox returned an invalid automation response.",
        )
        error = response[2]
        if error is not None:
            message = error.get("message", "") if isinstance(error, dict) else ""
            code = error.get("error", "") if isinstance(error, dict) else ""
            if (
                command == "Marionette:SetContext"
                and parameters.get("value") == "chrome"
            ) or (
                command in ("WebDriver:ExecuteScript", "WebDriver:ExecuteAsyncScript")
                and (
                    code in ("unsupported operation", "unknown command")
                    or isinstance(message, str)
                    and "system access" in message.lower()
                )
            ):
                raise StartupError(SYSTEM_ACCESS_MESSAGE, check="system_access")
            if isinstance(message, str) and any(
                word in message.lower()
                for word in (
                    "operation not permitted",
                    "permission denied",
                    "ns_error_file_access_denied",
                )
            ):
                raise PermissionDenied(PERMISSION_MESSAGE)
            raise BookmarkError("Firefox rejected the bookmark automation command.")
        return response[3]

    def script(self, script, args=None, asynchronous=False):
        response = self.send(
            (
                "WebDriver:ExecuteAsyncScript"
                if asynchronous
                else "WebDriver:ExecuteScript"
            ),
            {"script": script, "args": args or [], "scriptTimeout": 120000},
        )
        require(
            isinstance(response, dict) and "value" in response,
            "Invalid Firefox result.",
        )
        return response["value"]

    def __enter__(self):
        try:
            check_closed()
            require_gui_session()
            environment = os.environ.copy()
            for name in (
                "XRE_PROFILE_PATH",
                "XRE_PROFILE_LOCAL_PATH",
                "MOZ_APP_DATA",
                "MOZ_LOCAL_APP_DATA",
                "MOZ_LOG",
                "MOZ_LOG_FILE",
                "MOZ_MARIONETTE",
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS",
                "MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS",
            ):
                environment.pop(name, None)
            if self.root is not None:
                private_directory(self.root)
                for name, variable in (
                    ("app-data", "MOZ_APP_DATA"),
                    ("local-data", "MOZ_LOCAL_APP_DATA"),
                ):
                    directory = self.root / name
                    private_directory(directory)
                    environment[variable] = str(directory)
            self.port = free_port()
            environment["MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS"] = json.dumps(
                {"marionette.port": self.port, "marionette.log.level": "Fatal"}
            )
            environment["MOZ_REMOTE_ALLOW_SYSTEM_ACCESS"] = "1"
            self.launch_token = "about:blank#mac-setup-" + secrets.token_hex(16)
            command = [
                "/usr/bin/open",
                "-n",
                "-g",
                "-W",
                "-a",
                "/Applications/Firefox.app",
                "--stdin",
                "/dev/null",
                "--stdout",
                "/dev/null",
                "--stderr",
                "/dev/null",
            ]
            for name in (
                "MOZ_APP_DATA",
                "MOZ_LOCAL_APP_DATA",
                "MOZ_MARIONETTE_PREF_STATE_ACROSS_RESTARTS",
                "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS",
            ):
                if name in environment:
                    command.extend(["--env", name + "=" + environment[name]])
            command.extend(
                [
                    "--args",
                    "--headless",
                    "-no-remote",
                    "--marionette",
                    self.launch_token,
                ]
            )
            self.launcher = subprocess.Popen(
                command,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 60
            while self.process is None:
                self.process = FirefoxProcess.find(self.launch_token)
                if self.process is not None:
                    break
                if (
                    self.launcher.poll() not in (None, 0)
                    or time.monotonic() >= deadline
                ):
                    raise StartupError(LAUNCH_MESSAGE, check="launch")
                time.sleep(0.1)
            while self.connection is None:
                if self.process.poll() is not None:
                    replacement = FirefoxProcess.find(self.launch_token)
                    if replacement is None:
                        if time.monotonic() >= deadline:
                            raise StartupError(LAUNCH_MESSAGE, check="launch")
                        time.sleep(0.1)
                        continue
                    self.process = replacement
                try:
                    self.connection = socket.create_connection(
                        ("127.0.0.1", self.port), timeout=1
                    )
                except (ConnectionRefusedError, TimeoutError, socket.timeout):
                    require(
                        time.monotonic() < deadline,
                        "Firefox did not select a default profile within 60 seconds. Open Firefox at the screen, choose a default profile, and quit before retrying.",
                    )
                    time.sleep(0.1)
            self.connection.settimeout(130)
            if self.process.poll() is not None:
                replacement = FirefoxProcess.find(self.launch_token)
                require(replacement is not None, LAUNCH_MESSAGE)
                self.process = replacement
            wait_for_peer(self.connection, self.process, self.port, deadline)
            self.stream = self.connection.makefile("rb")
            hello = receive(self.stream)
            require(
                isinstance(hello, dict) and hello.get("marionetteProtocol") == 3,
                "Unsupported Firefox import protocol.",
            )
            self.version = observed_version(
                self.send("WebDriver:NewSession", {"capabilities": {}})
            )
            self.send("Marionette:SetContext", {"value": "chrome"})
            connected_pid = self.script("return Services.appinfo.processID;")
            if connected_pid != self.process.pid and self.process.poll() is not None:
                replacement = FirefoxProcess.find(self.launch_token)
                if replacement is not None:
                    self.process = replacement
                    wait_for_peer(self.connection, self.process, self.port, deadline)
            require(
                connected_pid == self.process.pid and self.process.poll() is None,
                "Firefox automation connected to an unexpected process.",
            )
            identity = self.script(
                IDENTITY_SCRIPT, [str(self.root) if self.root is not None else None]
            )
            if not isinstance(identity, dict) or identity.get("isolated") is not True:
                raise StartupError(ISOLATION_MESSAGE, check="isolation")
            profile_id = identity.get("profile_id")
            require(
                isinstance(profile_id, str)
                and re.fullmatch(
                    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", profile_id
                ),
                "Firefox returned an invalid profile identity.",
            )
            self.profile_key = hashlib.sha256(profile_id.encode("ascii")).hexdigest()
            self.identity_created = identity.get("identity_created") is True
            self.verified = True
            return self
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            if isinstance(error, BookmarkError) and not isinstance(
                error, (PermissionDenied, StartupError)
            ):
                raise StartupError(str(error)) from None
            raise

    def import_tree(self, tree):
        result = self.script(IMPORT_SCRIPT, [tree], asynchronous=True)
        require(
            isinstance(result, dict)
            and set(result) == {"imported"}
            and type(result["imported"]) is int
            and 0 <= result["imported"] <= 20000,
            "Firefox could not import the bookmarks. Check the export and retry.",
        )
        return result["imported"]

    def __exit__(self, kind, value, traceback):
        try:
            try:
                if self.process is not None and self.process.poll() is not None:
                    if kind is None:
                        raise BookmarkError(
                            "Firefox exited before the import could finish cleanly."
                        )
                elif (
                    self.process is not None
                    and self.connection is not None
                    and self.verified
                ):
                    try:
                        shutdown = self.send(
                            "Marionette:Quit", {"flags": ["eAttemptQuit"]}
                        )
                        require(
                            isinstance(shutdown, dict)
                            and shutdown.get("cause") == "shutdown"
                            and shutdown.get("forced") is False
                            and shutdown.get("in_app") is True,
                            "Firefox did not acknowledge normal shutdown after import.",
                        )
                        self.process.wait(timeout=30)
                    except (
                        OSError,
                        ValueError,
                        BookmarkError,
                        subprocess.SubprocessError,
                    ):
                        if kind is None:
                            raise BookmarkError(
                                "Firefox did not quit cleanly. Retry the import."
                            ) from None
            finally:
                if self.stream is not None:
                    self.stream.close()
                if self.connection is not None:
                    self.connection.close()
                try:
                    if self.process is None and self.launcher is not None:
                        self.process = FirefoxProcess.find(self.launch_token)
                    if self.process is not None and self.process.poll() is None:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.process.kill()
                            self.process.wait(timeout=10)
                finally:
                    if self.launcher is not None:
                        try:
                            self.launcher.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.launcher.terminate()
                            self.launcher.wait(timeout=10)
        except (OSError, ValueError, BookmarkError, subprocess.SubprocessError):
            if kind is None:
                raise
        return False


def state_root(firefox_root=None):
    if firefox_root is not None:
        return safe_path(Path(firefox_root) / "mac-setup-state")
    return safe_path(
        Path.home() / "Library/Application Support/mac-setup/firefox-bookmarks"
    )


def completion_path(firefox_root, profile_key):
    require(
        isinstance(profile_key, str) and re.fullmatch("[0-9a-f]{64}", profile_key),
        "Firefox returned an invalid completion identity.",
    )
    return state_root(firefox_root) / (profile_key + ".json")


def private_directory(path):
    try:
        path = safe_path(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_path(path, directory=True)
        require(path.stat().st_mode & 0o077 == 0, STATE_DIRECTORY_MESSAGE)
    except OSError as error:
        if error.errno in (errno.EACCES, errno.EPERM):
            raise
        raise StartupError(STATE_DIRECTORY_MESSAGE, check="state_directory") from None
    except BookmarkError:
        raise StartupError(STATE_DIRECTORY_MESSAGE, check="state_directory") from None
    return path


@contextmanager
def import_lock(firefox_root=None):
    if firefox_root is not None:
        private_directory(firefox_root)
    root = private_directory(state_root(firefox_root))
    descriptor = os.open(
        root / "import.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        require(
            os.fstat(descriptor).st_uid == os.getuid(),
            "Firefox import lock belongs to another account.",
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BookmarkError("Another bookmark import is running.") from None
        yield
    finally:
        os.close(descriptor)


def completed(firefox_root, profile_key):
    marker = completion_path(firefox_root, profile_key)
    if not marker.exists():
        require(not marker.is_symlink(), "Firefox import refuses symbolic links.")
        return False
    private_path(marker)
    require(
        marker.stat().st_mode & 0o077 == 0,
        "mac-setup bookmark completion needs mode 0600.",
    )
    record = json.loads(marker.read_text(encoding="utf-8"))
    require(
        isinstance(record, dict)
        and set(record) == {"version", "completed"}
        and type(record["version"]) is int
        and record["version"] == 1
        and record["completed"] is True,
        "Firefox completion record is invalid.",
    )
    return True


def record_completion(firefox_root, profile_key):
    marker = completion_path(firefox_root, profile_key)
    private_directory(marker.parent)
    with tempfile.NamedTemporaryFile(
        dir=marker.parent, prefix=".completion-", delete=False
    ) as output:
        temporary = Path(output.name)
        try:
            output.write(b'{"version": 1, "completed": true}\n')
            output.flush()
            os.fsync(output.fileno())
            output.close()
            os.replace(temporary, marker)
        finally:
            temporary.unlink(missing_ok=True)


def _permission_error(error):
    if error.errno in (errno.EACCES, errno.EPERM):
        raise PermissionDenied(PERMISSION_MESSAGE) from None
    raise BookmarkError(
        "Firefox bookmark import could not finish. Check Firefox and the source export, then retry."
    ) from None


def inspect_bookmarks(firefox_root=None, check=False):
    try:
        if not Path(FIREFOX).is_file():
            return {"changed": False, "imported": 0, "state": "not_installed"}
        if check:
            return {"changed": True, "imported": 0, "state": "would_import"}
        check_closed()
        with import_lock(firefox_root):
            with FirefoxSession(firefox_root) as session:
                profile_key = session.profile_key
                finished = completed(firefox_root, profile_key)
                changed = session.identity_created
        return {
            "changed": changed,
            "imported": 0,
            "state": "already_imported" if finished else "pending",
            "profile_key": profile_key,
        }
    except OSError as error:
        _permission_error(error)
    except (ValueError, UnicodeError, subprocess.SubprocessError):
        raise BookmarkError(
            "Firefox bookmark inspection could not finish. Check Firefox and retry."
        ) from None


def import_bookmarks(source, firefox_root=None, check=False, expected_profile_key=None):
    if check:
        return inspect_bookmarks(firefox_root, check=True)
    try:
        if not Path(FIREFOX).is_file():
            return {"changed": False, "imported": 0, "state": "not_installed"}
        check_closed()
        with import_lock(firefox_root):
            with FirefoxSession(firefox_root) as session:
                profile_key = session.profile_key
                require(
                    expected_profile_key is None or expected_profile_key == profile_key,
                    "Firefox selected a different profile. Retry the run before importing.",
                )
                finished = completed(firefox_root, profile_key)
                imported = 0
                if not finished:
                    source = private_path(source)
                    require(
                        source.stat().st_mode & 0o077 == 0,
                        "Decrypted bookmarks need mode 0600.",
                    )
                    require(
                        source.stat().st_size <= MAX_SOURCE,
                        "Bookmarks HTML exceeds 16 MiB.",
                    )
                    tree = parse_html(source.read_text(encoding="utf-8-sig"))
                    imported = session.import_tree(tree)
            if not finished:
                record_completion(firefox_root, profile_key)
        return {
            "changed": not finished,
            "imported": imported,
            "state": "already_imported" if finished else "imported",
        }
    except OSError as error:
        _permission_error(error)
    except (ValueError, UnicodeError, subprocess.SubprocessError):
        raise BookmarkError(
            "Firefox bookmark import could not finish. Check Firefox and the source export, then retry."
        ) from None
