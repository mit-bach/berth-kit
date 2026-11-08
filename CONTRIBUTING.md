# Contributing

Fork it, change it, and send a pull request. A few things to know first.

- **Where changes land.** This repository is published from Dominik's working copy of Berth, one version at a time. A pull request merged here is carried back into that copy before the next version is published, so the next version includes it.
- **Test first.** Run both suites before you send one (`docs/TROUBLESHOOTING.md`, "Run the tests"). Three Berth tests are known to fail (`docs/OPEN-ITEMS.md`); anything else failing is new.
- **Nothing private.** No tokens, passwords, keys, `.env` files, customer data, or session transcripts.
- **Your own choices stay in your clone.** Permission mode, effort, folders and model rules are per person (`docs/DECISIONS.md`). Change them in your copy, not in a pull request.
- **A bug report** says what you did, what you saw, what you expected, and how to make it happen again. `python3 tools/doctor.py` and `berth check` output help.
