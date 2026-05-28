# Chunk 6 Runbook — Facebook + Nextdoor via Claude in Chrome

These sources are login-walled and their Terms of Service prohibit automated
scraping. We never point a headless scraper at them. Instead, Claude in Chrome
reads **your own logged-in session** (functionally the same as you browsing),
and the results flow through `import_listings.py` — the same non-rental, geo,
and rental/roommate filters as the automated scrapers — then get scored by
`enrich.py`.

Run this roughly weekly (September is Milwaukee's tightest leasing month, so
inventory turns over fast).

---

## What works, what doesn't

| Source | Agent-driven? | Notes |
|---|---|---|
| Facebook Marketplace | ✅ Yes | Claude in Chrome can read your logged-in Marketplace. **Low yield** — the grid is mostly corporate apartments + commercial spam; thin data (no address/neighborhood). |
| Facebook Groups | ✅ Yes | Higher-quality posts (landlords write full text), but messier feed. Same extraction approach. |
| Nextdoor | ❌ No | Claude in Chrome blocks navigation to nextdoor.com. Gather manually (see below). |

**Reality check:** Craigslist remains the primary source for independent Tosa
duplexes — it produced the strongest hits (7–9/10). FB/Nextdoor are a backstop.
Don't over-invest here; skim for the occasional gem and move on.

---

## A. Facebook Marketplace (agent-driven)

1. In Claude (this project), confirm Chrome is connected and you're logged into Facebook.

2. Ask Claude to navigate to the Wauwatosa rentals search, newest first, under budget:
   ```
   https://www.facebook.com/marketplace/107596615929465/propertyrentals/?sortBy=creation_time_descend&maxPrice=1200
   ```

3. Claude runs this extraction snippet (scrolls to load, parses each card, drops
   the obvious junk so the payload fits):
   ```js
   (async () => {
     for (let i=0;i<4;i++){ window.scrollTo(0,document.body.scrollHeight); await new Promise(r=>setTimeout(r,1200)); }
     const FAR=/kenosha|racine|caledonia|genoa|kewaskum|west bend|sussex|slinger|union grove|new berlin|waukesha|oconomowoc|hartford|mukwonago|burlington|grafton|cedarburg|port washington|fond du lac|sheboygan|germantown|menomonee falls|hales corners|franklin|oak creek|cudahy|south milwaukee|muskego|elkhorn|saukville|waterford|lannon|lake geneva|pewaukee|brookfield/i;
     const seen=new Set(), out=[];
     document.querySelectorAll('a[href*="/marketplace/item/"]').forEach(a=>{
       const m=a.href.match(/\/marketplace\/item\/(\d+)/); if(!m||seen.has(m[1]))return; seen.add(m[1]);
       const p=a.innerText.split('\n').map(s=>s.trim()).filter(Boolean);
       let price=null,title=null,location=null;
       p.forEach(x=>{ if(/^\$[\d,]+/.test(x)&&price===null)price=x; else if(title===null)title=x; else if(location===null)location=x; });
       const n=price?parseInt(price.replace(/[^\d]/g,'')):0;
       if(n<=5||(location&&FAR.test(location)))return;
       out.push({url:'https://www.facebook.com/marketplace/item/'+m[1]+'/',rent:price,title,location});
     });
     window.__fb=out; return out.length;
   })();
   ```

4. Claude reads `window.__fb` (in slices if truncated) and writes it to
   `data/fb_marketplace_<date>.json` as a JSON array of
   `{url, rent, title, location}` objects.

5. Ingest + score:
   ```bash
   python import_listings.py --source facebook_marketplace data/fb_marketplace_<date>.json
   python enrich.py
   ```

The non-rental filter drops event spaces / offices / storage / salons; the geo
filter drops far suburbs; Haiku scores the rest. Open the dashboard, sort by
**Best fit**, filter **Min fit 5+**.

### Optional: deep-dive a promising FB item
The grid data is thin (no address/description). For a listing that looks
plausible, have Claude open its item URL and run `get_page_text` to capture the
full body (address, pet policy, parking, photos count). Re-ingest that single
listing with the richer `description` for a more accurate score.

---

## B. Facebook Groups (agent-driven, higher quality)

Same approach, but navigate to each group's feed and extract post text + links.
Highest-value Tosa/Milwaukee groups (from the research doc):

- Wauwatosa, WI: Buy Sell Discuss Real Estate — `facebook.com/groups/Wauwatosa/`
- Milwaukee Housing/Rooms/Apartments/Sublets — `facebook.com/groups/apartmentsinmilwaukee/`
- Milwaukee Roommates — `facebook.com/groups/MilwaukeeRoommates/`
- Milwaukee County Apartments for Rent — `facebook.com/groups/1404749289830969/`

Group posts are free-text, so capture the whole post body as `description` —
that gives Haiku much more to score on than the Marketplace grid.

---

## C. Nextdoor (manual — agent navigation blocked)

Claude in Chrome won't navigate to nextdoor.com. To include Nextdoor:

1. In your own browser, go to Nextdoor → **For Sale & Free → Housing/Rentals**,
   filtered to your neighborhoods (Tosa, Story Hill, Washington Heights, the Village).
2. For each relevant post, copy the post URL + text.
3. Build a JSON array `[{url, title, description, location, rent}]` and save it
   as `data/nextdoor_<date>.json`.
4. Ingest + score:
   ```bash
   python import_listings.py --source nextdoor data/nextdoor_<date>.json
   python enrich.py
   ```

---

## Weekly cadence

```
1. python scraper.py        # refresh Craigslist + PM sites (primary source)
2. (Claude in Chrome)       # FB Marketplace + Groups extraction → data/*.json
3. python import_listings.py --source facebook_marketplace data/fb_*.json
4. python enrich.py         # score everything new (pay-once; ~pennies)
5. Open dashboard, sort Best fit, Min fit 5+, review new strong candidates
```
