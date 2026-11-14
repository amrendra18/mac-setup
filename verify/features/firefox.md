# Firefox policies

This journey tests the Firefox role through the real site playbook.
It uses made-up settings and disposable browser profiles.
The settings are test inputs, separate from the operator's pending baseline review.

## Run the journey

1. Use a Mac set aside for testing with Firefox installed in `/Applications/Firefox.app`.
2. Quit Firefox in every account. The helper refuses an existing Firefox process and never terminates it.
3. Make sure the account has password-free administrator access for the existing verification harness.
4. Allow Firefox to download uBlock Origin from `addons.mozilla.org` and follow its download redirects.
5. Run:

```sh
scripts/verify fast --feature firefox
```

The journey temporarily replaces the machine policy plist and the role's ownership record and process-lock file.
It saves all three originals under the private `.local/firefox-journey/` folder before changing either store.
After the snapshot, setup clears the test machine policy domain and imports exactly the two seed policies.
It reads them back before testing bookmark import.
The harness always runs cleanup, including after a failed setup or playbook.
Cleanup restores the original bytes, file mode, owner, group, and modification time.
A store absent before the journey is absent afterwards.
Failed restoration keeps the snapshot and blocks another setup.

## What the journey checks

1. Before import blocking, Firefox imports exactly one bookmark from made-up HTML through `BookmarkHTMLUtils`.
   `PlacesUtils` reads the imported URL and title back from the disposable profile.
2. A check run changes zero policy keys. All three files keep their exact bytes and file metadata.
3. Apply stores the requested boolean, string, and dictionary values with their exact types.
   A planted `DontCheckDefaultBrowser` policy stays in place.
4. The second apply reports `changed=0` and changes none of the three files' bytes nor metadata.
5. Each browser run reads the real `about:policies` page, checks expected active policy names, and requires zero errors.
   Firefox's typed active policies must also match the expected settings.
6. The **Active** tab of `about:policies` shows `browser.mac-setup.owner` with `mac-setup artificial journey`.
   The active policy and effective preference also contain that exact artificial label.
7. Five preferences have their exact expected lock states.
   These are homepage, history clearing, clear-on-quit, Global Privacy Control, and the owner label.
   The locked `Preferences` entries use `Status: locked`.
8. The artificial `firefox.addons.install` input requests the public add-on `uBlock0@raymondhill.net`.
   Its exact `ExtensionSettings` entry uses the AMO latest-XPI URL.
   The helper waits up to 120 seconds for AddonManager to report uBlock Origin active in each disposable profile.
   Locked mode uses `force_installed` and prevents the user from disabling uBlock Origin.
   Editable mode uses `normal_installed` and lets the user disable it.
9. The lock transition removes the owned import restriction and both native locks.
   Global Privacy Control and the owner label change to editable `Status: user`.
   All five checked preferences become editable, while the unrelated policy remains.
10. Artificial `user.js` overrides retain a different homepage and history choice in unlocked mode.
   The shared reader must report the history override as a startup value conflicting with the stored policy.
11. One artificial history entry exists before normal quit and is absent after reopening that same disposable profile.
12. Browser cleanup requests normal quit first. On failure it terminates only the subprocess it started.

## Isolation and evidence

1. Firefox keeps its signed application identity and reads the real machine policy domain.
   Each launch receives separate `MOZ_APP_DATA` and `MOZ_LOCAL_APP_DATA` folders beside its disposable profile.
2. Marionette binds to loopback and controls only that test process.
   Only the test child's environment sets `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1` for privileged inspection.
   Firefox 158 through 160 support this variable. The command-line flag was removed in Firefox 159.
   The parent environment and ordinary Firefox launches keep their existing access settings.
3. The harness records commands, exits, and recaps under the `.local/verify/` evidence folder it prints.
   Filtered policy assertions, browser logs, disposable profiles, and count summaries remain under `.local/firefox-journey/`.
   Policy proof files contain only known test policy names, successful assertions, and counts.
   Extension evidence reports an installed count and whether the user can disable the artificial add-on.
   The helper never saves raw policy-page rows or unrelated active policy values.
   Each disposable launch saves `runtime.json` with the sanitized version reported by its Marionette session.
   This records the running browser, which can differ from the catalog version after an automatic update.
4. Cleanup removes the saved original files after verifying restoration.
   It retains only the artificial test profiles and proof files.

## Gotchas and limits

1. The journey changes policies for every account while it runs. Keep all ordinary Firefox instances closed until cleanup finishes.
2. External managed policies or installation policies can change the result.
   A differing active value, remaining lock, or policy error fails the journey.
3. The journey needs working Marionette startup and Firefox's current privileged bookmark and history APIs.
   Synthetic tests prove framing, assertions, and restoration without launching Firefox.
   They provide no evidence of browser acceptance.
4. The bookmark check proves HTML import before import blocking.
   One-time import, interrupted-import recovery, and the encrypted bookmark file belong to the later bookmark milestone.
5. uBlock Origin is a public test input. The journey downloads the version AMO serves at run time.
   A network failure, download refusal, inactive add-on, or unexpected disable permission fails the journey.
   Synthetic tests do not prove network installation or Firefox policy acceptance.
6. DNS behavior, other clearing categories, and the operator's final settings remain outside this fixture.
   Main runs the journey on the test Mac and records its exact Firefox version and evidence folder.
   A rejected Marionette command reports its fixed command name, script proof label, and standard error category.
   It also reports the running browser version when the session provides a valid version.
   Remote messages, stack traces, script text, arguments, paths, and policy values stay out of that error line.
7. If cleanup fails, resolve the stated cause and run this exact command from the checkout before another journey:

```sh
/usr/bin/python3 -I -B verify/helpers/firefox.py restore
```

Mozilla's [Firefox 158 implementation](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_158_0_1_RELEASE/remote/components/RemoteAgent.sys.mjs)
already reads the child environment grant.
The [Firefox 159](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_159_0_RELEASE/remote/components/RemoteAgent.sys.mjs)
and [Firefox 160](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/remote/components/RemoteAgent.sys.mjs)
implementations retain that grant and remove the old flag.
Firefox 160's [context guard](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/remote/marionette/driver.sys.mjs)
still requires system access for chrome context.

The extension fixture uses uBlock Origin to keep session creation independent of an external first-run page.
Its [startup code](https://github.com/gorhill/uBlock/blob/master/src/js/start.js)
initializes existing tabs and has no welcome-page navigation.
Firefox Color's [background initialization](https://github.com/mozilla/FirefoxColor/blob/30579235c423022b841858e0bb7171fbe9fc057f/src/extension/background.js)
opens its external first-run page in each fresh profile.
Firefox 160's [session creation](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/remote/marionette/driver.sys.mjs)
waits for the initial selected tab's navigation.

The helper names the failed proof for both assertions and unexpected errors.
The session script timeout is 30000 milliseconds; the socket timeout is 45 seconds.
These are separate limits. The helper does not reuse a connection after a transport timeout.
Apple Python 3.9 socket timeouts retain the `timeout` category.

The policy page renders nested preference values over several rows.
The owner-label proof follows the Preferences group and its owner preference through those continuation rows.
It requires the exact label in that preference's Value cell, for both locked and user status.
Firefox's [page renderer](https://github.com/mozilla-firefox/firefox/blob/FIREFOX_160_0_RELEASE/browser/components/enterprisepolicies/content/aboutPolicies.js)
keeps later policy and preference names blank in continuation rows.
