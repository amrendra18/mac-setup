# One-time Firefox bookmarks

This journey runs the real site playbook against four disposable Firefox targets.
It encrypts made-up bookmarks with a random test password.
It never uses the operator's bookmark export or vault password.

## Run the journey

1. Use a Mac set aside for testing with `/Applications/Firefox.app` installed.
2. Quit Firefox in every account. The helper refuses an existing Firefox process.
3. Ensure the verification account has password-free administrator access.
4. Run this command from the checkout.

```sh
scripts/verify fast --feature bookmarks
```

## What the journey checks

1. Setup occupies Marionette's default port `2828` and starts a disposable Firefox through the real importer.
   Firefox must choose another port and quit normally before the control releases its listener.
   An existing service on port `2828` stays untouched.
   Setup records the session's reported Firefox version, independently of the catalog and browser-map test baseline.
   Before preview, `reads/bookmarks-startup-control.json` receives both control booleans and that version, or `unknown`.
   This version describes setup. Firefox can update between sessions.
2. Preview leaves the fresh Firefox target absent.
3. Apply lets Firefox select its profile and imports exactly three artificial bookmarks.
4. Firefox reads each bookmark's exact title, address, parent folder, and position through its Places API.
   The three links occupy two ordered folders inside a toolbar folder.
   Readback compares the whole imported folder layout, including folder names and positions.
   Failed readback names the invalid result, active policy, source copy counts, titles, existing count, extra entries, folder tree, or preservation.
   Each message omits private bookmark values.
5. The active `DisableProfileImport` policy remains true during those reads.
6. A second apply reports `changed=0` and preserves the external completion records.
   Firefox starts briefly to identify its selected profile and may update its own housekeeping files.
7. Replacing the encrypted source with four bookmarks still leaves three bookmarks after completion.
   The extra bookmark stays absent, and the run reports `changed=0`.
8. Removing the completion record simulates interruption after Firefox committed its bookmarks.
   Retrying recreates the record and leaves exactly one copy of each bookmark.
9. A third target starts with only the first source bookmark and no completion record.
   Its production import adds the two missing bookmarks without duplicating the first.
   Its second apply reports `changed=0`.
10. Another disposable profile starts with one existing artificial bookmark inside a known folder.
   A separator gives that bookmark a known position within its folder.
   Setup saves every existing entry's identifier, parent, position, type, title, and address before import.
   Readback requires the same ordered existing entries after import and adds the three source bookmarks.
   Its second apply reports `changed=0`.
11. The importer refuses a deliberately malformed encrypted test export after decryption, with exit code `2`.
    Cleanup requires no decrypted temporary directory, completion record, or artificial imported row.
    Firefox may initialize an empty profile while the importer inspects the selected target.
12. Every import removes its private temporary directory.
   Cleanup checks the Ansible logs and evidence for zero copies of artificial titles, addresses, and the random vault password.

## Isolation and cleanup

1. Setup snapshots the machine Firefox policy, the role's ownership record, and its lock file.
   It uses the existing Firefox journey's byte and metadata restoration checks.
   Setup copies and seeds policies without calling that journey's browser setup or profile feasibility probe.
2. The temporary machine policy enables enterprise policies, skips the default-browser prompt, and sets `DisableProfileImport=true`.
   These policies affect every account until cleanup restores them.
3. The role receives only four directories under `.local/bookmarks-journey/` as its target.
   Each target uses its canonical absolute path and mode `0700` before creating its state directory.
   Seeds and probes use the importer's `FirefoxSession(root)` and native Firefox profile selection.
   Each target has separate `app-data` and `local-data` directories through Firefox environment variables.
   The helper never reads `profiles.ini` or the protected Firefox directory.
   Firefox supplies an opaque profile key through its own API.
   Completion records live in each target's `mac-setup-state` directory, outside Firefox data.
   The startup control adds a separate disposable target beside the four import targets.
   Its listener binds only loopback and stays open until the controlled Firefox process quits.
   Every browser start in setup uses the production `FirefoxSession(root)` with a disposable target.
   Sessions use LaunchServices with explicit `open --env` values and independently verified Firefox process identifiers.
   The session environment includes `MOZ_REMOTE_ALLOW_SYSTEM_ACCESS=1` for privileged bookmarks operations.
   The account must remain signed in at the Mac's screen, including when the journey command runs over SSH.
4. Setup creates encrypted Netscape HTML in memory.
   Only encrypted exports, the random test password, and private expected values remain under `.local/bookmarks-journey/`.
   The directory has mode `0700`. Each private file has mode `0600`.
5. Each generated inventory receives only an encrypted source under its parent `files/` folder.
   The journey declares `ANSIBLE_VAULT_PASSWORD_FILE` for setup, every playbook, and cleanup.
   The harness applies that value to each command without changing later journeys.
   Each run also passes the same file through `--vault-password-file`.
   Setup refuses a missing or different selection before changing policies.
   The helper prints fixed check messages and suppresses private exception details.
   Session errors during setup name the default-port startup control or the specific Firefox startup or permission check.
   Separate fixed messages identify unsafe target-directory permissions and a selected Firefox profile outside its isolated target.
   A setup session error never reports an import operation.
   A refusal of privileged context or scripts keeps the fixed `system_access` guidance during startup and cleanup.
6. Evidence contains commands, recaps, safe count summaries, and encrypted test input.
   Cleanup restores the policies and deletes the entire private journey folder, including disposable profiles and passwords.
7. The harness always invokes cleanup after a failed setup or run.
   Cleanup also removes a leftover decrypted file when its bytes match the artificial test input.
   Unknown temporary contents remain untouched. Cleanup retains private state and refuses another setup until recovery.
   A failed policy restoration preserves its snapshot and prevents another setup.

## Limits and recovery

1. Synthetic harness tests use fake browser responses and temporary files.
   Node also executes the actual Places probe against a synthetic bookmark API.
   Deliberately moved bookmarks, reordered bookmarks, moved folders, reordered folders, and renamed folders fail that probe.
   The imported-entry controls cover fresh and existing profiles.
   Separate controls move and reorder existing entries and folders.
   The declared-step test denies file access to a synthetic protected Firefox directory with `EPERM`.
   Setup, all 10 runs, and cleanup must leave that directory untouched.
   Synthetic setup also refuses any call to the older browser launcher or profile feasibility probe.
   These tests prove structure assertions, count assertions, private output checks, encryption, isolation, and cleanup behavior.
   They provide no evidence that Firefox accepts the import.
   Version controls cover 158.0.1, 159.0, 160.0, a future version, and unknown or unsafe version values.
   The catalog and browser-map baseline remains Firefox 158.0.1 until native verification supports a change.
2. Run the command above on the test Mac and record its evidence folder and Firefox version.
   External managed policies and Firefox API changes can cause the real journey to fail.
   An import into an existing profile does not prove fresh disposable targets work.
   A fresh target must have mode `0700` before its lock and completion directories are created.
   Default-profile access through LaunchServices over SSH needs a separate run with a configured account source.
   The disposable journey proves isolated imports and policy restoration; it does not access the owner's profile.
3. Interruption cases cover one committed bookmark and all three committed bookmarks before the completion record.
   They do not kill Firefox during a database transaction.
4. If cleanup cannot restore the policies, resolve the reported cause and run this command before another journey.

```sh
ANSIBLE_LOG_PATH=/dev/null .local/venv/bin/python -I -B verify/helpers/bookmarks.py cleanup
```
