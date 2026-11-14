# Firefox policies

This journey tests the Firefox role through the real site playbook.
It uses made-up settings and disposable browser profiles.
The settings are test inputs, separate from the operator's pending baseline review.

## Run the journey

1. Use a Mac set aside for testing with Firefox installed in `/Applications/Firefox.app`.
2. Quit Firefox in every account. The helper refuses an existing Firefox process and never terminates it.
3. Make sure the account has password-free administrator access for the existing verification harness.
4. Run:

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
6. Four preferences have their exact expected lock states.
   These are homepage, history clearing, clear-on-quit, and Global Privacy Control.
   The locked `Preferences` entry uses `Status: locked`.
7. The lock transition removes the owned import restriction and both native locks.
   The Global Privacy Control entry changes to editable `Status: user`.
   All four checked preferences become editable, while the unrelated policy remains.
8. Artificial `user.js` overrides retain a different homepage and history choice in unlocked mode.
   The shared reader must report the history override as a startup value conflicting with the stored policy.
9. One artificial history entry exists before normal quit and is absent after reopening that same disposable profile.
10. Browser cleanup requests normal quit first. On failure it terminates only the subprocess it started.

## Isolation and evidence

1. Firefox keeps its signed application identity and reads the real machine policy domain.
   Each launch receives separate `MOZ_APP_DATA` and `MOZ_LOCAL_APP_DATA` folders beside its disposable profile.
2. Marionette binds to loopback and controls only that test process.
   The test process uses `--remote-allow-system-access` to inspect preferences and artificial Places data.
3. The harness records commands, exits, and recaps under the `.local/verify/` evidence folder it prints.
   Filtered policy assertions, browser logs, disposable profiles, and count summaries remain under `.local/firefox-journey/`.
   Policy proof files contain only known test policy names, successful assertions, and counts.
   The helper never saves raw policy-page rows or unrelated active policy values.
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
5. Extension installation, DNS behavior, other clearing categories, and the operator's final settings remain unverified by this fixture.
   Main runs the journey on the test Mac and records its exact Firefox version and evidence folder.
6. If cleanup fails, resolve the stated cause and run this exact command from the checkout before another journey:

```sh
/usr/bin/python3 -I -B verify/helpers/firefox.py restore
```
