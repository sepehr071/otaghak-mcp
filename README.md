<!-- mcp-name: io.github.sepehr071/otaghak-mcp -->

<div align="center">

<img src="https://raw.githubusercontent.com/sepehr071/otaghak-mcp/main/.github/banner.png" alt="otaghak-mcp: let your AI agent find the real price of a stay on Otaghak" width="100%">

# 🏡 otaghak-mcp

**Let your AI agent find a place to stay on Otaghak.**<br>
Search villas, cottages and apartments across Iran, check which nights are free, get the exact price<br>
for your dates and guests, and read reviews and host stats, all from Claude, Cursor or Copilot.

[![PyPI](https://img.shields.io/pypi/v/otaghak-mcp?color=2563eb)](https://pypi.org/project/otaghak-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/otaghak-mcp)](https://pypi.org/project/otaghak-mcp/)
[![CI](https://github.com/sepehr071/otaghak-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/sepehr071/otaghak-mcp/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP_Registry-io.github.sepehr071%2Fotaghak--mcp-7c3aed)](https://registry.modelcontextprotocol.io/?q=otaghak-mcp)
[![License: MIT](https://img.shields.io/badge/license-MIT-16a34a)](https://github.com/sepehr071/otaghak-mcp/blob/main/LICENSE)

[![Install in Cursor](https://cursor.com/deeplink/mcp-install-dark.svg)](https://cursor.com/en/install-mcp?name=otaghak&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJvdGFnaGFrLW1jcCJdfQ==)
[![Install in VS Code](https://img.shields.io/badge/VS_Code-Install_otaghak--mcp-0098FF?style=flat-square&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect/mcp/install?name=otaghak&config=%7B%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22otaghak-mcp%22%5D%7D)

[Quick start](#quick-start) · [What it can do](#what-it-can-do) · [Tools](#tools) · [FAQ](#faq) · [فارسی](#فارسی)

</div>

---

## Why

On Otaghak the price on a listing card is rarely what you pay: every night has its own price, weekends cost
more, discounts change per night, extra guests cost extra per night, and calendars only open about a month
ahead. Getting *the real total for my dates and my group* means clicking through calendars one room at a time.
An agent with `otaghak-mcp` does the clicking:

> **You:** We are 4 people, Ramsar, 20 to 23 October. How much is the "سوییسی لیلی" cottage in total?
>
> **Agent:** *calls* `ot_search_rooms(city=["ramsar"], name="لیلی")` → `ot_price_quote(room_id=2508916, check_in="2026-10-20", check_out="2026-10-23", guests=4)`
>
> | Night | Price | Before discount |
> |---|---:|---:|
> | Tue 20 Oct | 4,000,000 | 5,000,000 |
> | Wed 21 Oct | 4,000,000 | 5,000,000 |
> | Thu 22 Oct | 4,250,000 | 5,000,000 |
> | 1 extra guest (3 nights) | 980,000 | 1,200,000 |
> | **Total to pay** | **13,230,000** | |
>
> The cottage includes 3 guests and takes at most 4, so the 4th person costs 400,000 a night (discounted like the
> nights). All three nights are free and bookable instantly. Want me to compare it with `ot_similar_rooms`?

<sub>Real tool output from 2026-10-04; prices change all the time. Prices are in Toman.</sub>

## What it can do

- 🔎 **Search** stays in a city, province or themed collection (beach, jungle, villa with pool, ...) for your dates and group, sorted by price, rating or discount
- 🧮 **Exact price** for dates and guests: every night after discount, extra-guest charges, optional late checkout
- 📅 **Calendars**: which nights are free, nightly prices, minimum and maximum stay, weekends and holidays
- 🏠 **Room details**: rooms and beds, amenities, house rules, check-in times, cancellation policy, nearby shops, photos
- ⭐ **Quality**: rating, six category scores, percent who recommend it, reviews with host replies, host response time and acceptance rate
- 🏷️ **Deals**: last-second discounts and the most discounted stays
- 🧭 **Guides**: destination guides, FAQ, blog articles and the site's rules (cancellation, refunds)
- 🔒 **Read-only by design**: no login, no booking, no payment, no SMS codes

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/).

<details open>
<summary><b>Claude Code</b></summary>

```bash
claude mcp add otaghak -- uvx otaghak-mcp
```
</details>

<details>
<summary><b>Claude Desktop</b></summary>

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "otaghak": { "command": "uvx", "args": ["otaghak-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Cursor</b></summary>

Click **Install in Cursor** above, or add the Claude Desktop block to `~/.cursor/mcp.json`.
</details>

<details>
<summary><b>VS Code (Copilot agent mode)</b></summary>

Click **Install in VS Code** above, or add to `.vscode/mcp.json`:

```json
{
  "servers": {
    "otaghak": { "type": "stdio", "command": "uvx", "args": ["otaghak-mcp"] }
  }
}
```
</details>

<details>
<summary><b>Anything else</b></summary>

It's a standard stdio MCP server: run `uvx otaghak-mcp`, or `pip install otaghak-mcp` and run `otaghak-mcp`.
</details>

Then just ask:

- "Cheapest villa with a pool near Ramsar for 6 people next weekend?"
- "Is room 2512254 free from 20 to 23 October, and what's the total for 5 guests?"
- "Pet-friendly cottages in Gilan with a rating of 4 or more."
- <span dir="rtl">یک کلبه جنگلی در ماسال برای ۴ نفر، آخر هفته بعد، با رزرو آنی پیدا کن.</span>

## How it works

```text
  AI agent  (Claude, Cursor, Copilot, ...)
      │
      │  MCP over stdio
      ▼
  otaghak-mcp  (runs on your machine)
      │
      │  HTTPS (REST + OData)
      └──────▶  core.otaghak.com, www.otaghak.com/blog
```

`otaghak-mcp` runs locally and calls the same public endpoints the otaghak.com website uses.
There's no hosted server in between, no API key, and nothing about you is sent anywhere else.

## Tools

Rooms are identified by numeric ids like `2512254` (the number in `otaghak.com/room/2512254/`), places by slugs
like `ramsar` (city), `mazandaran` (province) or `beach` (collection).

<details open>
<summary><b>🔎 Search</b> (5)</summary>

| Tool | What it does |
|---|---|
| `ot_find_place` | Place name → city / province slug; a room code → that room |
| `ot_search_rooms` | Stays in a city, province or collection for dates and guests, with filters and sorting |
| `ot_search_filters` | Filter codes (pool, parking, pets, beach or jungle area, ...), room types, price range, landmarks |
| `ot_destinations` | Popular cities and collections, and the cities of a collection |
| `ot_deals` | Last-second deals and the most discounted stays |
</details>

<details open>
<summary><b>🏠 Room and host</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ot_room` | Details, amenities, rules, check-in/out times, cancellation policy, host, location, photos |
| `ot_reviews` | Rating, star histogram, category scores, percent who recommend, reviews with host replies |
| `ot_similar_rooms` | 4-5 alternatives, re-priced and checked for your dates and guests |
| `ot_host` | Host response time, response and acceptance rates, other rooms, newest reviews |
</details>

<details open>
<summary><b>📅 Dates and prices</b> (4)</summary>

| Tool | What it does |
|---|---|
| `ot_room_calendar` | Day by day: free or booked, price, min/max nights, weekend, holiday, late checkout |
| `ot_price_quote` | Exact amount to pay for dates and guests, extra guests and late checkout included |
| `ot_holidays` | Iranian public holidays in a date range, with Jalali dates |
| `ot_late_checkout` | Whether a room offers late checkout, hours, price, and if it's open on your departure day |
</details>

<details open>
<summary><b>🧭 Help and guides</b> (2)</summary>

| Tool | What it does |
|---|---|
| `ot_help` | Help-center FAQ, guest terms (booking, cancellation policies, refunds, wallet), host rules |
| `ot_travel_guide` | Destination guide, its FAQ, related search pages and blog articles |
</details>

All 15 tools are annotated `readOnlyHint: true` and return compact structured JSON, so they don't flood the agent's context.

## Good to know

- **Prices are in Toman**, as on the site (1 Toman = 10 Rial). `price_per_night` is after discount. Without dates, prices are the room's undated starting price, not a quote.
- **The amount to pay** is `ot_price_quote` → `total`: the nights after discount (the room page's payable amount), plus extra guests above the room's included guests (discounted like the nights), plus optional late checkout. Coupons and wallet credit need a login and are not included. Search results carry a `stay_total` estimate that can be a few Toman off; with dates, `sort="cheapest"` orders each page by it.
- **Dates** are Gregorian `YYYY-MM-DD` and `check_out` is the departure day. No past dates, at most 20 nights, and hosts' calendars open only to the end of the next Jalali month; the tools say so clearly instead of returning an empty list. A Jalali date gets an error with its Gregorian twin.
- **Guests:** each room includes `base_guests` in its price and takes up to `max_guests`. `ot_price_quote` checks the limit itself (the site's own search doesn't when you ask for a specific room).
- **Ratings are 0–5**; `null` means not rated yet. Holiday flags are only published about 7 weeks ahead.
- **Location** is approximate (about 100 m); the exact address and the host's phone come only after booking.
- **Persian queries match best** (`رامسر`, `کلبه`), English slugs work too.

## FAQ

<details>
<summary><b>Can it book a room for me?</b></summary>

No, and that's deliberate. It has no login and never calls booking, payment, OTP/SMS, wallet, favorites or review
endpoints. The agent finds the room and the exact price; you book on otaghak.com.
</details>

<details>
<summary><b>Why does a search for December return an error?</b></summary>

Hosts' calendars on Otaghak are open only to the end of the next Jalali month, and stays are limited to 20 nights.
Outside that window the site has no prices, so the tools tell you the last bookable day instead.
</details>

<details>
<summary><b>Do I need an Iranian IP?</b></summary>

No. It was tested from an Iranian home connection and through a Turkish exit, and both worked. Cloud servers
outside Iran were not tested; if Otaghak blocks one, set `OTAGHAK_MCP_PROXY`.
</details>

<details>
<summary><b>I get "did not answer in time" or "Could not reach"</b></summary>

The server retries a failed connection once (timeouts are not retried). If it keeps failing, check your connection
or set `OTAGHAK_MCP_PROXY`. Normal system proxy variables are ignored on purpose, because direct calls are the fastest.
</details>

<details>
<summary><b>Claude Desktop says <code>uvx</code> is not found</b></summary>

Use the full path to `uvx` (`where uvx` on Windows, `which uvx` on macOS/Linux) as `command`.
</details>

<details>
<summary><b>How do I debug what the agent sees?</b></summary>

```bash
npx @modelcontextprotocol/inspector uvx otaghak-mcp
```
</details>

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `OTAGHAK_MCP_PROXY` | unset | HTTP proxy for every request, e.g. `http://user:pass@host:port` |

## فارسی

<div dir="rtl">

**otaghak-mcp** به دستیار هوش مصنوعی شما (Claude، Cursor، Copilot و ...) اجازه می&zwnj;دهد در اتاقک ویلا، کلبه و
آپارتمان جستجو کند، شب&zwnj;های خالی را ببیند، مبلغ دقیق اقامت را برای تاریخ و تعداد نفرات شما حساب کند و نظرات و
وضعیت میزبان را بخواند.

- فقط خواندنی است: وارد حساب نمی&zwnj;شود، رزرو و پرداخت نمی&zwnj;کند و کد پیامکی نمی&zwnj;فرستد.
- مبلغ نهایی را با قیمت تک&zwnj;تک شب&zwnj;ها، هزینه نفر اضافه و تأخیر در تخلیه حساب می&zwnj;کند.
- همه قیمت&zwnj;ها به تومان است و تاریخ&zwnj;ها میلادی (YYYY-MM-DD) هستند.
- روی سیستم خود شما اجرا می&zwnj;شود و به هیچ سرور واسطی داده نمی&zwnj;فرستد.

**نصب در Claude Code:**

</div>

```bash
claude mcp add otaghak -- uvx otaghak-mcp
```

<div dir="rtl">

بعد بپرسید: «ارزان&zwnj;ترین ویلای استخردار رامسر برای ۶ نفر از ۲۰ تا ۲۳ اکتبر چند تمام می&zwnj;شود؟»

</div>

## Development

```bash
git clone https://github.com/sepehr071/otaghak-mcp && cd otaghak-mcp
uv sync
uv run pytest            # offline, against recorded responses
uv run pytest -m live    # real API
uv run ruff check .
```

Tools live in `src/otaghak_mcp/search.py`, `room.py`, `pricing.py` and `info.py`; each is a typed async function
with a docstring that tells the agent when to use it. Issues and PRs are welcome, especially new tools and fixes for
API changes.

Releases: bump the version in `pyproject.toml` and `server.json`, then push a `v*` tag. GitHub Actions tests,
publishes to PyPI and the [MCP Registry](https://registry.modelcontextprotocol.io), and creates the GitHub Release.

## Disclaimer

Unofficial and not affiliated with or endorsed by Otaghak. It uses the public endpoints of the otaghak.com website,
which can change without notice. Otaghak offers an official booking web service to partners under contract; this is
not it. Please keep request rates reasonable.

## License

[MIT](https://github.com/sepehr071/otaghak-mcp/blob/main/LICENSE)
