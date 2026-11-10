# Poke watch

Checks Greek shops every hour for **English** Pokémon TCG **30th Celebration** sealed products (boxes,
tins, packs, collections) and pushes an alert to your phone when one is in stock or open for pre-order
**at retail price**.

What it watches:

- **Public**: its search, plus its 11 own 30th Celebration product pages opened directly on every run,
  because Public's search hides sold-out products (that's how the €89.90 ETB would slip past)
- **Kaissa** (the Greek Pokémon TCG distributor), **Nerdom**, **efantasy**, **iConnect** and
  **Moustakas Toys**, directly on their own sites
- **BestPrice**, which lists offers from dozens of Greek shops at once (Roll n Trade, Toys24, Toys4u,
  PerfecToys, Gamescom, OtakuStore and many more), so any shop selling at retail gets caught

"Retail" is decided by a price cap per product type (`PRICE_CAPS` in the script), set from Public's and
Kaissa's prices. Ignored on purpose: anything over the cap, any language other than English (Japanese,
Chinese, Korean, German, French...), single cards (even English ones), cases, store displays, empty
boxes, Pokémon Center exclusives, Public Partner (marketplace) sellers, and stock a shop only sells over
the counter in its own stores. It never buys anything, it just tells you.

## 1. Phone alerts

1. Install the **ntfy** app (Android or iOS) and subscribe to a topic with a long random name,
   e.g. `manos-poke-8f3k2q7x`. Anyone who knows the name can read it, so don't pick something guessable.
2. Copy `poke_watch.env.example` to `poke_watch.env` and put the topic in it:
   ```
   NTFY_TOPIC=manos-poke-8f3k2q7x
   ```
   Telegram works too (`TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`), alongside or instead of ntfy.

## 2. First run on your PC

```
pip install -r requirements.txt
python -m playwright install chromium
python poke_watch.py --notify-test      # a push should land on your phone
python poke_watch.py --test             # shows what every shop returns, sends nothing
```

In the `--test` output, `[BUY]` lines are what would trigger an alert, and every product shows the
price it read, your cap and where the stock status came from. If a shop says `check FAILED`:

- `python poke_watch.py --test --shop Public --headed` opens a visible browser so you can watch it
- `python poke_watch.py --test --shop Public --debug` saves screenshots and HTML to `debug/`

The first real run will probably fire straight away, because some 30th items are on sale at retail
right now (see below). When a run finds more than 3 things you get one summary push instead of many.

## 3. Run it every hour

The setup that works: **GitHub checks most shops around the clock, and your PC checks Public, Kaissa
and Nerdom**, because those three block GitHub's servers ("Access Denied" or a bot check). The GitHub
workflow already skips them (`--skip-shop "Public,Kaissa,Nerdom"`).

### A. GitHub Actions: every other shop, even when your PC is off

1. Create a new **public** repository. Actions minutes are free for public repos; a private repo gets
   2,000 free minutes a month, which hourly runs would go through, so there change the cron line in
   `.github/workflows/poke-watch.yml` to `17 */4 * * *` (every 4 hours, since each run takes several minutes).
2. Upload everything in this folder, including the `.github` folder. Not `poke_watch.env`.
3. Settings > Secrets and variables > Actions > New repository secret: `NTFY_TOPIC` = your topic.
4. Actions tab > Poke watch > Run workflow, to try it once. Leave "Send a test push" ticked: a test push
   reaches your phone within a minute or two, then the real check runs.

After each run the job commits `state.json`, which is how it remembers what it already told you.
GitHub pauses schedules in public repos after 60 days without activity; if that happens, one click on
"Enable workflow" in the Actions tab turns it back on.

GitHub runs the job from its own servers abroad. If another shop starts blocking them (you'd get a
"check failing" push after 6 runs), add it to `--skip-shop` in the workflow and to `SHOPS` in `setup_pc.py`.

### B. Your Windows PC: Public, Kaissa and Nerdom

1. Put this folder somewhere permanent that OneDrive doesn't sync, e.g. `C:\Users\<you>\poke-watch`.
2. Double-click `setup_pc.bat`. If Windows asks whether to run it, choose Run (or More info > Run anyway).
   It installs what it needs (Python packages and a browser), asks for your ntfy topic, sends a test
   push, checks the three shops once so you see whether they let it in, and adds an hourly task (at
   :47) to Windows Task Scheduler. Needs Windows 10 or 11 (64-bit) and Python 3.10 to 3.13 from
   python.org; it tells you if Python is missing. Playwright is pinned to 1.63.0, the version this was tested with.
3. That's it. It checks while the PC is on and awake. What each run did goes to `poke_watch.log`.
   To pause it, double-click `remove_pc_task.bat` (removes only the scheduled task).
   To remove everything, double-click `uninstall_pc.bat`, then delete the folder.

**What setup changes on your PC:** the folder itself gets `.venv` (a private Python environment),
`browsers` (Chromium, downloaded by Playwright), `poke_watch.env` (your topic), `state.json` and
`poke_watch.log`; and Windows gets one scheduled task, "Poke watch", for your account only, at limited
rights, that runs `pythonw.exe poke_watch.py --shop Public,Kaissa,Nerdom` from this folder. No admin
rights, registry edits, PATH changes or system settings.

**What it reads:** its own folder, and the shop websites. Chromium starts with a blank temporary profile,
so it can't see your Chrome or Edge profile, saved passwords, cookies or Windows credentials.
**What it talks to:** the shop sites (plus a few of their own content and search hosts), `ntfy.sh` for the
push (or your own `NTFY_SERVER`), `api.telegram.org` only if you set Telegram, and PyPI and Playwright's
download servers at install time. Analytics, ads, social, payment and sign-in hosts are blocked in the
browser. Run `python poke_watch.py --test --hosts` to see exactly which hosts each shop check loaded or blocked.
**Logs:** the log has shop names, product URLs and errors. Your topic, tokens and chat id are replaced with
`<hidden>` if they ever appear in it.

### C. Mac, Linux or a Raspberry Pi

`crontab -e` and add:

```
7 * * * * cd /path/to/poke-watch && python3 poke_watch.py >/dev/null 2>&1
```

Or just leave `python3 poke_watch.py --every 60` running in a terminal.

## 4. Greek retail prices and who sells at them (checked 7 Nov 2026)

Public's own 30th Celebration prices. Only the Mini Tin was in stock; the rest showed "temporarily sold
out" at these prices.

| Product | Public | Cap in the script |
|---|---|---|
| Ultra-Premium Collection | €299 | €320 |
| Elite Trainer Box | €89.90 (Kaissa €90) | €95 |
| Ditto Premium Collection | €74.90 (Kaissa €65) | €80 |
| Binder Collection | €69.90 | €75 |
| Booster Bundle | €44.90 | €50 |
| ex Box, ex Tin (Sylveon or Greninja) | €39.90 | €44 |
| Poster Collection, Tech Sticker Collection | €34.89 | €38 |
| Mini Tin | €24.90 (in stock) | €27 |
| 2-Pack Blister | €19.99 | €22 |
| Figure Collection | (Kaissa €50) | €55 |
| Battle Deck | (Kaissa €35) | €38 |

Resellers were asking 1.5x to 2x these (ETB €150 to €195). Knock Out Collection and single booster packs
are estimates.

Shops seen at or near retail that day: **Kaissa**, **Public**, **Nerdom** (its Pokémon range within €1
of Kaissa's prices), **efantasy** (Delta Reign sleeved booster €5.99, same as Public), **Toys4u**
(Delta Reign ETB €74.99), **PerfecToys**, **Toys24**, **Gamescom**, **OtakuStore** and **Funnybunny**
(30th Battle Deck €34.90 to €35), **Roll n Trade** (30th Mini Tins €24.90, though well above retail for
the big boxes).

Not quite retail: **iConnect** prices big boxes above the others (Delta Reign ETB €84.90 against Public's
€74.90, Phantasmal Flames ETB €149.90) and sells some stock in its physical stores only; the caps and
the store-only rule handle that. Its 30th listings so far are single cards and Japanese or Chinese
boxes, and efantasy's are Chinese, so neither has anything English to alert on yet. Moustakas showed
no Pokémon cards in its online search.

## 5. Tuning

Everything is in the SETTINGS block at the top of `poke_watch.py`:

- `PRICE_CAPS`: the highest price you count as retail, per product type.
- `SHOPS`: add a shop with its home page and `"search": None`; the script finds the search box itself
  and remembers the URL. `product_link` keeps out books, figures and so on. Paste product URLs into a
  shop's `"watch"` list to have them opened on every run (useful when a shop's search hides sold-out items).
- `NOT_ENGLISH` and `SKIP_NAMES`: what gets ignored by name.
- `ALERT_ON`: remove `"preorder"` if you only want things that ship now, or add `"store_only"` to hear
  about stock a shop only sells in its physical stores.
- `REMIND_HOURS`, `NOTIFY_NEW_LISTINGS`, `MAX_SEPARATE_ALERTS`, `EXTRA_PRODUCTS` (e.g. `"pokemon day 2026"`).

## What the pushes look like

- **In stock: Elite Trainer Box at Public**: urgent, tap to open the product page
- **Pre-order open: Ultra-Premium Collection at Kaissa**
- **5 Pokémon 30th items at retail price**: summary when a run finds several, with buttons for the top 3
- **New 30th listing at Nerdom**: quiet, a shop listed something even if you can't buy it yet
- **Poke watch: Public check failing**: 6 failed runs in a row (blocked, or the site changed)

Once an hour and a handful of pages per shop is polite. Don't run it every minute; shops block that.
