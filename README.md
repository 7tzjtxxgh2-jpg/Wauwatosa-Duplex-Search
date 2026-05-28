# Finding a Wauwatosa Duplex or Room Rental for September 2026: Channels & Semi-Automation Playbook

> **Note:** This document is the original **research & planning playbook**. The
> system that was actually built diverged from it in several places (Flask+SQLite
> instead of Google Sheets, Craigslist HTML instead of RSS, a Quick Add box
> instead of auto-scraping Facebook, etc.). For how the code actually works, see
> **[ARCHITECTURE.md](ARCHITECTURE.md)**. For the weekly Facebook/Nextdoor
> routine, see **[RUNBOOK_chrome.md](RUNBOOK_chrome.md)**.

**TL;DR**
- The big national portals (Zillow / Apartments.com / Hotpads / Trulia) capture only a slice of Tosa's duplex stock; the bulk lives on **Facebook Marketplace + a handful of FB Groups, Craigslist Milwaukee (with RSS), college off-campus boards (Marquette, MCW, UWM), and small landlord/realtor websites** (Krista Crowder/wauwatosaduplex.com, Bieck, MPI, RENU, Ogden, Atari, Welcome Home Milwaukee, Shorewest). Yard signs and word-of-mouth in the Village/East Tosa/Story Hill are still material.
- The right semi-automation stack is **(1) email digests + Gmail filters fed into Claude API for parsing, (2) Claude-in-Chrome for the login-walled sites (Facebook, Nextdoor, MCW credentialed list), and (3) a weekly–to–6-hourly GitHub Actions cron scraping Craigslist's RSS + a handful of small-PM sites**, with hits flowing into a single Google Sheet plus a GitHub Pages frontend modeled on the user's PhilJobs Dashboard.
- Quick-start (one weekend): create saved searches with email alerts on Zillow/Apartments/Hotpads/Realtor/Rent.com, subscribe to Craigslist's RSS via a GitHub Actions cron, join ~8 named Facebook groups, register on Marquette + MCW + UWM off-campus portals, and add the four or five small Tosa PMs' "available rentals" pages to a 3x/week Chrome-agent check. Expect first hits within 7–14 days.

---

## Key Findings

1. **The "Duplex" property type filter is a trap.** Tosa duplex halves get listed on national portals as "Apartment," "House," "Upper," "Lower," or "Flat" far more often than as "Duplex." Filter by bedrooms + price + Wauwatosa polygon and visually scan instead. Apartments.com's Wauwatosa "Duplex" filter, for instance, returned only ~4 listings versus 30 in its FRBO (for-rent-by-owner) view of the same city.
2. **Three channels carry the majority of true small-landlord Tosa inventory**: Craigslist Milwaukee, Facebook Marketplace + 2–3 Tosa/Milwaukee FB groups, and the MCW credentialed housing list. National portals catch the corporate apartment complexes (Tosa Village, Mayfair Reserve, Harwood, Serafino Square, Echelon, Watertown, Overlook, 1600Tosa, Hawthorne Terrace) and the sliver of landlords using Avail/TurboTenant/Zillow Rental Manager.
3. **Wauwatosa has no public rental registry** — unlike the City of Milwaukee, which operates a DNS Property Registration program. Cold-outreach to Tosa duplex owners must be done parcel by parcel through the Wauwatosa property-information portal; Milwaukee-side parcels (Washington Heights / Story Hill east of 60th) can be looked up in bulk through the Milwaukee assessor and DNS Land Management System.
4. **Craigslist still works in Milwaukee** and is the single most automatable channel — RSS feeds are publicly published, listings are dense, and a search for "wauwatosa" in the apartments subforum returns dozens of 1–2 BR units in your price band right now.
5. **MCW and Marquette are leverage**. MCW sits inside Wauwatosa (8701 W Watertown Plank Rd) and runs an internal, email-gated landlord list (`mcwhousing@mcw.edu`) where many Tosa duplex owners post first because they prefer the MCW-resident tenant profile. Marquette's off-campus portal is publicly powered by Rent College Pads and exposes ~105 listings extending west into Tosa.

---

## Section 1 — Ranked List of Advertising Channels

For each channel: URL, what's listed, automation amenability. Ranking reflects estimated Tosa-duplex hit-rate × ease-of-monitoring at the user's $1,200 solo / $1,600 couple / room ≤$700 budget.

### Tier A — Highest signal for Tosa duplexes

**1. Facebook Marketplace — Wauwatosa rentals**
- Apartments: https://www.facebook.com/marketplace/107596615929465/apartments-for-rent/
- Houses: https://www.facebook.com/marketplace/107596615929465/houses-for-rent/
- Combined: https://www.facebook.com/marketplace/107596615929465/propertyrentals/
- Mom-and-pop Tosa duplex owners post here first. Observed prices on current Wauwatosa results span ~$400–$2,600, with $850–$1,500 typical for 1–2 BR Tosa-area duplex halves.
- Automation: **Login-walled, no API.** Use Claude-in-Chrome with a saved query URL on your own logged-in session.

**2. Craigslist Milwaukee — apartments/housing**
- All apartments: https://milwaukee.craigslist.org/search/apa
- Wauwatosa-filtered: https://milwaukee.craigslist.org/search/apa?query=wauwatosa
- Duplex keyword: https://milwaukee.craigslist.org/search/apa?query=duplex
- Rooms/shares: https://milwaukee.craigslist.org/search/roo
- Observed Tosa duplex rents in current listings: $1,200–$1,700 for 1–2 BR upper/lower units (e.g., 8535 W Hawthorne Ave, 2376 N 63rd St, 2645 N 63rd St, 2661 N 62nd St, 11611 W Elmhurst Pkwy).
- Automation: **Each search URL exposes an RSS feed via `&format=rss`.** This is the single most automatable channel and a safe-harbor for personal-use polling.

**3. Krista Crowder Real Estate — wauwatosaduplex.com**
- https://www.wauwatosaduplex.com/info/ and /contact-us/
- The most explicitly Wauwatosa-duplex-focused boutique broker (also handles small-portfolio property management). Phone (414) 678-9131; LennisMathews@gmail.com. Related FB page: https://www.facebook.com/61577578054818/ ("Edward Crowder Rentals").
- Automation: Static page; weekly fetch via GitHub Actions, diff for new listings.

**4. Marquette Off-Campus Housing portal (powered by Rent College Pads)**
- https://www.marquette.edu/off-campus/find-housing.php → https://www.rentcollegepads.com/off-campus-housing/marquette/search
- ~105 listings; bleeds west into Tosa.
- Automation: Saved-search email alerts; stable HTML; scrape-friendly.

**5. MCW Student Housing list — credentialed, email-gated**
- Landing: https://www.mcw.edu/education/academic-and-student-services/student-housing
- Access: email **mcwhousing@mcw.edu**; landlords post via **propertylisting@mcw.edu**.
- MCWAH housestaff: https://www.mcw.edu/education/graduate-medical-education/mcwah-housestaff-life/housing-rental-information
- Why it matters: MCW is *in* Tosa. Many duplex owners specifically target MCW residents and never list elsewhere.
- Automation: Email-gated. Request access, then route Gmail → filter → Claude parser.

**6. UWM Off-Campus Housing Marketplace**
- https://rentoffcampus.uwm.edu/listing
- https://uwm.edu/housing/off-campus-housing/
- Includes Shorewood/Riverwest plus some Tosa-adjacent west-side listings (LiveHere/College Pads back end).
- Automation: Public HTML; scrape-friendly.

**7. RentCollegePads — MCW board**
- https://www.rentcollegepads.com/off-campus-housing/medical-college-wisconsin/search
- Claimed 188+ listings near Froedtert/MCW — heaviest Tosa concentration of any single board.

### Tier B — Material supply, easy to automate via email

**8. Zillow Rental Manager / Zillow + Trulia + Hotpads (one network)**
- Posting via Zillow Rental Manager auto-syndicates to all three.
- Automation: Saved-search email alerts work cleanly.

**9. Apartments.com (+ ApartmentFinder, ForRent, Apartment List — all CoStar)**
- Wauwatosa hub: https://www.apartments.com/wauwatosa-wi/
- Duplex filter (~4 listings): https://www.apartments.com/wauwatosa-wi/duplex/
- FRBO (~30 listings): https://www.apartments.com/wauwatosa-wi/for-rent-by-owner/

**10. Realtor.com / Rent.com / Redfin / Zumper / Apartment Finder**
- These ingest from **Avail and TurboTenant** syndication. Per TurboTenant's official help center, TurboTenant pushes to Realtor.com, Rent.com, Redfin, etc., but **NOT directly to Zillow** — Zillow leads it tags as "Zillow Group" are coming via HotPads or Trulia. Avail syndicates to Zillow, Trulia, Realtor.com, Apartments.com, and Zumper.
- Practical implication: small Tosa landlords using Avail show up nearly everywhere; landlords using only Zillow Rental Manager appear on Zillow/Trulia/Hotpads only. Cover both flavors.

**11. Shorewest Rentals**
- https://www.shorewest.com/rentals; Elmbrook-Wauwatosa office at 11430 W North Ave: https://www.shorewest.com/wisconsin-real-estate-offices-wauwatosa-brookfield-elm-grove
- Shorewest **was founded in 1946 as "Wauwatosa Realty" by John A. Horning out of his home in Wauwatosa** and rebranded in 1997; today it operates more than 20 offices across Wisconsin and dominates Tosa MLS share. Individual Tosa-resident agents (e.g., Beth Jaworski) are themselves landlords and frequently source off-market duplexes.

### Tier C — Local property managers worth scraping/checking weekly

**12. Bieck Management** — https://bieckmanagement.com; tenant portal https://bieck.twa.rentmanager.com. Tosa coverage confirmed via Apartments.com PMC listing; mostly apartments, some duplexes.
**13. MPI Property Management** — https://www.mpiwi.com. Per MPI's own About page: founded 1978, currently manages **over 1,100 residential units in Southeastern Wisconsin** with a 32-person full-time staff, including single-family, condos, **duplexes**, and multi-family.
**14. RENU Property Management (wisconsinpm.com)** — https://www.renupropertymgt.com — confirmed Tosa duplexes (e.g., 8415 Stickney Ave).
**15. Atari Property Management** — https://www.ataripropertymanagement.com — family-owned; manages single-family, duplexes, multi-unit; partners with AppFolio.
**16. Welcome Home Milwaukee** — https://whmilwaukee.com — scattered-site PM, formerly MKE PM + REIS (merged 2014).
**17. Ogden & Company** — https://ogdenre.com; rental portal https://www.rentalsogdenrent.com. Founded 1929. Lists single-family homes, condos, **duplexes**, and apartments. (Note: Ogden also developed Elevation 1659 and similar multifamily; their small-property inventory shows up on the RENTCafe portal above.)
**18. Founders 3 Management** — https://founders3.com/apartments/ — larger portfolios, occasional Tosa.
**19. Renters Warehouse Milwaukee** — https://www.renterswarehouse.com/offices/milwaukee — explicitly lists Wauwatosa among its service cities; scattered-site SFR/duplex focus.
**20. My-Dwelling / Pennybag / Structure Properties / PMI of Greater Milwaukee** — scattered-site managers (see Expertise.com Milwaukee list); spot-check weekly.
**21. Berrada Properties** — https://www.berradaproperties.com. **Important caveat**: per Wisconsin Public Radio (Dec. 18, 2024), AG Josh Kaul announced a **$1.7 million consent judgment** ending a Wisconsin DOJ lawsuit (Case filed Nov. 15, 2021) against Berrada — Kaul: *"This is a landmark agreement for housing in the Milwaukee area… I believe the $1.7 million settlement is the largest settlement for a housing case in state history."* Berrada operates roughly **9,000 units across 200+ LLCs** in Milwaukee and Racine; the settlement also requires vacating and sealing **~3,250 qualifying eviction judgments** (per Wisconsin DATCP FAQ, updated Feb. 28, 2025). Mostly north/central Milwaukee complexes, **less Tosa-specific**. Worth a low-priority check; vet carefully.

Automation for Tier C: most managers use AppFolio, RentManager, or Buildium back-ends with a "Vacancies" page. Weekly GitHub Actions cron + Python `requests`/`BeautifulSoup` is fine — public marketing pages, polite request rate.

### Tier D — Niche / supplementary (login-walled)

**22. Named Facebook Groups** (Chrome-agent only):
- Wauwatosa, WI: Buy Sell Discuss Real Estate — https://www.facebook.com/groups/Wauwatosa/
- Wauwatosa Buy Sell and Trade — https://www.facebook.com/groups/463785000495851/
- Milwaukee Roommates: Rooms For Rent, Apartments, and Sublets — https://www.facebook.com/groups/MilwaukeeRoommates/
- Milwaukee (WI) – Housing, Rooms, Apartments, Sublets — https://www.facebook.com/groups/apartmentsinmilwaukee/
- Milwaukee County Apartments for Rent — https://www.facebook.com/groups/1404749289830969/
- Milwaukee Housing, Rooms, Apartments, Sublets, Roommates — https://www.facebook.com/groups/2072980819395293/
- MILWAUKEE – Housing, Apartments, Rooms, Sublets — https://www.facebook.com/groups/298996841150010/
- UW-Milwaukee Apartment, Roommate, and Sublease Finder — https://www.facebook.com/groups/540035606168684/
- UWM Sublet and Roommate Board – Milwaukee — https://www.facebook.com/groups/400770353381560/
- Marquette University Student Housing and Apartments — https://www.facebook.com/groups/marquetteuniversity/
- Marquette University (MU) Housing and Sublease — https://www.facebook.com/groups/1095363981762277/
- Marquette Housing, Looking for a house? Need Sub.. — https://www.facebook.com/groups/2263672620/
- Tosa East Towne Neighborhood Association (page, 3,349 likes) — https://www.facebook.com/TosaEastTowne/

**23. Nextdoor — Wauwatosa neighborhoods** (login-walled, address-verified)
- City of Wauwatosa officially links to Nextdoor as a community channel (https://www.wauwatosanac.org/tosa-links/). Duplex owners post "available" notices in Story Hill, Washington Heights, the Village, and East Tosa.
- Automation: Chrome agent only — no RSS.

**24. Roommate boards** (for the room/$700 plan):
- SpareRoom Wauwatosa — https://www.spareroom.com/rooms-for-rent/wi/milwaukee_county/wauwatosa (~21 rooms, 18 ads)
- SpareRoom Milwaukee — https://www.spareroom.com/rooms-for-rent/wi/milwaukee_county/milwaukee (~56 listings)
- Roomster Milwaukee — https://roomster.com/roommates/milwaukee (~95 listings; paid to contact)
- Roomies (Roommates.com) — https://www.roomies.com/milwaukee-wi
- iROOMit — https://www.iroomit.com/roommates/milwaukee-wi
- Roomsurf UWM — https://www.roomsurf.com/university-of-wisconsin-milwaukee-roommates
- Uloop UWM roommates — https://uwm.uloop.com/roommates
- Craigslist rooms — https://milwaukee.craigslist.org/search/roo

**25. Powers Realty Group** — https://www.powersrealty.com — boutique luxury brokerage with a Wauwatosa office at 7734 Harwood Ave. Primarily *sales*, but useful for knowing off-market duplex opportunities. Tosa-focused agents like Beth Jaworski at Shorewest (https://www.yelp.com/biz/beth-jaworski-shorewest-realtors-wauwatosa) and Lexington Realty Group (7935 Harwood Ave) play a similar role.

**26. Defunct or empty channels — skip**:
- **Wauwatosa NOW classifieds**: The Wauwatosa NOW newspaper's classifieds are dead; content was absorbed into https://www.jsonline.com/communities/west/ with no classifieds module. Archive only at https://archive.wauwatosanow.com/.
- **OnMilwaukee** has no rentals classifieds board.
- **Shepherd Express** (https://shepherdexpress.com) — Milwaukee's alt-weekly; no active online rental classifieds.

**27. Yard signs and word-of-mouth.** In Tosa this is a real channel and **no aggregator photographs these**. Streets to drive monthly: the grid east of 70th between Center and Bluemound (the duplex belt), the Village core (Harwood/State/76th), Story Hill (W. Wells/N. 60th/Story Pkwy), and Washington Heights on the Milwaukee side of the border south of Vliet. Look for hand-lettered "For Rent — call ___" signs and laminated flyers on light poles. A 20-minute drive on a Saturday returns several phone numbers Zillow will never see.

### Tier E — Public records (for direct outreach)

**28. City of Milwaukee assessor / DNS** (for Milwaukee-side parcels in 53208/53210/53213):
- Assessment search: https://assessments.milwaukee.gov/
- DNS Property Information (LMS): https://city.milwaukee.gov/DNS/Property-Information.htm
- Accela portal: https://aca-prod.accela.com/MILWAUKEE/Default.aspx
- Property Registration Program (mandatory registration of non-owner-occupied properties — useful to identify duplex owners): https://city.milwaukee.gov/DNSPrograms/PropertyRegistration

**29. Wauwatosa property information** (no rental registry exists):
- Property search / tax portal: https://www.wauwatosa.net/government/departments/administration/property-information
- Wauwatosa has only short-term-rental (Airbnb) inspection programs via the Health Dept and standard building/property-maintenance code enforcement — no public rental-property registry equivalent to Milwaukee DNS.

---

## Section 2 — Semi-Automation Architecture

### A. Email-Based Automation (the backbone)

```
[Saved searches on Zillow, Apartments.com, Hotpads, Trulia, Realtor.com,
 Rent.com, Zumper, RentCollegePads (Marquette + MCW), UWM portal,
 Shorewest, Bieck, MPI, RENU, Atari, Welcome Home]
                  │
                  ▼  (one Gmail label per source, plus a master "Rentals" label)
            ┌──────────────┐
            │  Gmail (MCP) │
            └──────────────┘
                  │
                  ▼  (cron, every 30 min)
   ┌──────────────────────────────────────┐
   │ Python pipeline                      │
   │  1. Gmail MCP: pull new "Rentals/*"  │
   │  2. Claude API parser →              │
   │     {address, neighborhood, beds,    │
   │      rent, available_date, url,      │
   │      source, contact, duplex_flag}   │
   │  3. Filter: rent ≤ 1200 solo OR      │
   │     ≤ 1600 if "2BR+"; area ∈         │
   │     {Wauwatosa, Washington Heights,  │
   │      Story Hill, 53213, 53226,       │
   │      53208 west of 35th, 53210};     │
   │     available 2026-08 ↔ 2026-10      │
   │  4. Upsert to Google Sheets          │
   └──────────────────────────────────────┘
                  │
                  ▼
   ┌──────────────────────────────────────┐
   │ GH Pages dashboard + daily digest    │
   └──────────────────────────────────────┘
```

**Setup steps:**
1. Gmail label tree: `Rentals/{Zillow,Apartments,Hotpads,Trulia,Realtor,Rent,Zumper,Craigslist,Marquette,MCW,UWM,Shorewest,PM}`.
2. On each platform, create a saved search: Wauwatosa + ZIPs 53213, 53226, 53208, 53210, 53222; max $1,700 (to catch borderline 2BR); min 1BR; any property type; daily digest enabled.
3. Gmail filters route by sender domain (zillow.com → `Rentals/Zillow`, etc.) and apply the master `Rentals` label.
4. Python script (cron'd locally or via GitHub Actions) calls Gmail MCP `list_messages` + `get_message`, then Claude API (Haiku for cost) with a structured JSON-output prompt:
   > *"Extract from this rental listing email: address, neighborhood (Wauwatosa | Washington Heights | Story Hill | East Tosa | West Allis border | Milwaukee west side | other), beds, baths, rent (number), available_date (ISO), listing_url, contact, source, duplex_flag (true if 'duplex'/'upper'/'lower'/'flat'/'2-family' present). Return JSON array if multiple."*
5. Upsert into Google Sheets via gspread; dedup key = `listing_url`.

### B. Chrome-Agent Automation (for login-walled sites)

**Targets:** Facebook Marketplace + named FB Groups, Nextdoor (Wauwatosa neighborhoods).

**Cadence:** Mon / Wed / Sat morning (~5–10 min each).

**Reusable prompt:**

> *"You are checking rental listings for me. I want a duplex, small house, or room rental in Wauwatosa, Wisconsin or immediately adjacent Washington Heights, Story Hill, East Tosa, West Allis border, or ZIPs 53208/53210/53213/53226/53222. Budget: $1,200/mo solo, $1,600/mo with a roommate, or any room ≤$700. Move-in: September 2026 (July–October 2026 acceptable).*
>
> *Visit each URL below. On each page, sort by 'newest' if available and scan the most recent 30 posts. For any post that plausibly matches, copy: poster name, link, address or neighborhood, rent, beds, available date, and the first 2 sentences. Output a JSON array. Ignore sales, services, and non-rentals.*
>
> *URLs:*
> 1. *https://www.facebook.com/marketplace/107596615929465/propertyrentals/?sortBy=creation_time_descend&maxPrice=1700*
> 2. *https://www.facebook.com/groups/Wauwatosa/*
> 3. *https://www.facebook.com/groups/apartmentsinmilwaukee/*
> 4. *https://www.facebook.com/groups/MilwaukeeRoommates/*
> 5. *https://www.facebook.com/groups/1404749289830969/*
> 6. *https://www.facebook.com/groups/540035606168684/  (UWM)*
> 7. *https://www.facebook.com/groups/1095363981762277/  (Marquette)*
> 8. *https://nextdoor.com/news_feed/?post_type=for_sale_and_free  (filter Tosa neighborhoods)*
> 9. *https://www.wauwatosaduplex.com/info/*
> 10. *https://www.shorewest.com/rentals?city=Wauwatosa"*

Capture the JSON output by having the agent paste into a `chrome_agent_runs` tab in your master Sheet, or copy to clipboard and append via a 30-line local Python script.

### C. Scraper-Based Automation (GitHub Actions + Python, PhilJobs pattern)

**Scrape-safe targets** (RSS or stable public HTML):
- **Craigslist Milwaukee RSS** (primary):
  - `https://milwaukee.craigslist.org/search/apa?query=wauwatosa&format=rss`
  - `https://milwaukee.craigslist.org/search/apa?query=duplex&format=rss`
  - `https://milwaukee.craigslist.org/search/apa?postal=53213&search_distance=3&format=rss`
  - `https://milwaukee.craigslist.org/search/apa?postal=53226&search_distance=3&format=rss`
  - `https://milwaukee.craigslist.org/search/roo?format=rss`
- **wauwatosaduplex.com** — static HTML diff
- **Shorewest rentals** — https://www.shorewest.com/rentals (parameterized)
- **MPI vacancies** — https://www.mpiwi.com
- **RENU** — https://www.renupropertymgt.com
- **Atari** — https://www.ataripropertymanagement.com
- **Welcome Home** — https://whmilwaukee.com
- **Bieck** — RentManager portal listing page
- **Ogden** — https://www.rentalsogdenrent.com
- **UWM marketplace** — https://rentoffcampus.uwm.edu/listing
- **RentCollegePads (Marquette, MCW)** — public search endpoints

**Workflow architecture (mirrors PhilJobs Dashboard):**
- `.github/workflows/scrape.yml` on cron `0 */6 * * *` (every 6h) and `workflow_dispatch`.
- `scrape.py` iterates a `sources.yaml` config (URL, parser=rss|html), uses `feedparser` for RSS and `httpx + selectolax` for HTML, normalizes to one schema.
- New rows → Claude API (Haiku or Sonnet) with structured-output prompt: confirm location, extract beds/rent/available, set `duplex_flag`, score 0–10 on fit.
- Append to `data/listings.jsonl` and Google Sheets via service account.
- A second workflow regenerates `docs/index.html` (GitHub Pages) with sortable table — identical pattern to PhilJobs.

**Polite-scraper hygiene:** UA `"rental-search-personal-use (contact: <email>)"`; honor robots.txt; ≤1 req/sec; cache with ETag/If-Modified-Since. Craigslist publishes RSS specifically for personal-use polling — that is the safe-harbor.

### D. Centralized Dashboard

**Recommendation: Google Sheets as the database + a tiny GitHub Pages frontend.**
- Sheets > Notion/Airtable here because gspread is trivial, `IMPORTRANGE`/filter views are mobile-friendly for showing-up-to-an-open-house decisions, and you already have a Google identity.
- Schema: `first_seen, source, url, address, neighborhood, beds, baths, rent, available, contact, duplex_flag, fit_score, status (new|emailed|toured|passed), notes`.
- Pinned filter views:
  - **Solo (≤$1,200)**: rent ≤ 1200 AND beds ≥ 1 AND area ∈ target
  - **With roommate (≤$1,600 total, ≥2BR)**: rent ≤ 1600 AND beds ≥ 2
  - **Room (≤$700)**: source ∈ {SpareRoom, FB Roommates, Craigslist roo} AND rent ≤ 700
  - **September 2026 window**: available between 2026-08-01 and 2026-10-15
- GitHub Pages page reads the Sheet's published-CSV endpoint, renders a sortable table with neighborhood badges and a Leaflet/Mapbox map. Daily email digest via an Apps Script trigger.

### E. Legal / TOS Considerations

- **Facebook (Marketplace + Groups)**: ToS prohibits automated scraping; however, the legal precedent here is more permissive than the corporate posture suggests. **Note the actual outcome of *Meta Platforms v. Bright Data*, Case No. 3:23-cv-00077-EMC (N.D. Cal.): on January 23, 2024, Judge Edward Chen *granted summary judgment for Bright Data*, ruling that Meta's Terms "do not bar logged-off scraping of public data"; Meta dropped its remaining claim on February 23, 2024** (Courthouse News Service 1/23/24; TechCrunch 2/26/24). That said, *logged-in* automated scraping is still risky and can lead to account suspension. **The right posture is Claude-in-Chrome operating manually on your own logged-in session** — functionally equivalent to your own browsing.
- **Zillow / Trulia / Hotpads**: ToS prohibits scraping; aggressive bot-blocking. **Email alerts only.**
- **Nextdoor**: ToS prohibits scraping; requires verified residence. **Chrome agent only.**
- **Craigslist**: Hostile to HTML crawlers (*Craigslist v. 3Taps*, *Craigslist v. PadMapper*), but **publishes RSS for personal use**. Use RSS; do not crawl HTML; stay <1 req/sec.
- **Apartments.com / Realtor.com / Rent.com / Zumper**: ToS prohibits scraping; robust email alerts. Use email.
- **Small PM websites (MPI, RENU, Atari, Bieck, Welcome Home, Ogden, etc.)**: Public marketing pages; no specific anti-scraping clause beyond generic ToS. Polite low-frequency fetching for personal use is standard. Identify yourself in the UA.
- **Marquette / MCW / UWM portals**: Marquette explicitly doesn't endorse and provides Rent College Pads externally; UWM marketplace is public. Polite scraping fine. **Do not bypass the MCW email gate.**
- **Public records (Milwaukee assessor, DNS Property Registration)**: Public; use the lookups individually for outreach — no bulk needed for a personal search.

---

## Section 3 — Quick-Start Recommendation (First Weekend)

**Saturday morning (90 min) — email alerts:**
1. Build the Gmail label tree.
2. Set saved-search alerts on **Zillow, Apartments.com, Realtor.com, Rent.com, Zumper, Hotpads, Trulia, Apartment Finder, ForRent** for Wauwatosa + ZIPs 53213, 53226, 53208, 53210, 53222; max $1,700; min 1BR; daily digest.
3. Add saved-search alerts on **RentCollegePads Marquette** (https://www.rentcollegepads.com/off-campus-housing/marquette/search) and **RentCollegePads MCW** (https://www.rentcollegepads.com/off-campus-housing/medical-college-wisconsin/search); create an account on **rentoffcampus.uwm.edu**.
4. Email **mcwhousing@mcw.edu** requesting access to the MCW credentialed housing list.

**Saturday afternoon (60 min) — Facebook + Nextdoor:**
5. Join the 8 Facebook groups in Tier D. Turn on group post notifications for the three highest-volume (Milwaukee Roommates; Milwaukee Housing/Rooms/Apartments/Sublets; Wauwatosa Buy Sell Discuss Real Estate).
6. Create a Nextdoor account; subscribe to For Sale & Free → Housing in Tosa, Washington Heights, Story Hill.

**Sunday morning (3 hours) — automation v1:**
7. Create GitHub repo `tosa-rentals` modeled on PhilJobs Dashboard.
8. Add `scrape.py` polling the five Craigslist RSS feeds + `wauwatosaduplex.com` + `shorewest.com/rentals`. Use `feedparser` + `httpx`. Commit `sources.yaml`.
9. Add Claude API parsing + `fit_score`. Set system prompt with your budget, neighborhoods, move-in window.
10. Wire to Google Sheets via gspread + service account; Apps Script emails daily digest of `fit_score ≥ 7`.
11. Cron `0 */6 * * *`.

**Sunday afternoon (60 min) — Chrome runbook:**
12. Save the Chrome-agent prompt from Section 2B as a snippet. Schedule Mon/Wed/Sat runs; output appends to the `chrome_agent_runs` tab.
13. Plan a Saturday Tosa drive: duplex belt 60th–76th north of Bluemound; the Village; Story Hill. Log yard-sign numbers in the Sheet under `source=yard_sign`.

**Expected first-hit timeline:** Email alerts + Craigslist RSS within 24–72 hours. FB Marketplace via Chrome agent typically surfaces 3–5 plausible listings/week in this price band. MCW credentialed access usually 1–2 business days. Posting an "ISO" in 2 Tosa-focused FB groups frequently produces direct landlord DMs within 7–10 days.

---

## Recommendations

- **Lead with Craigslist RSS + Facebook Marketplace + MCW** — these three channels carry the bulk of true small-landlord Tosa inventory. National portal alerts are a backstop, not the primary feed.
- **Don't use the "Duplex" property-type filter** on Zillow/Apartments — Tosa duplex halves are routinely listed as Apartment, House, Upper, or Lower. Filter by bedrooms + price + the Wauwatosa polygon.
- **Treat Avail/TurboTenant as a positive signal**: weirdly-formatted phone numbers on Realtor.com / Rent.com / Zumper often indicate a small landlord using landlord software — usually responsive, often negotiable on small things.
- **Cold-outreach playbook for off-market**: identify a target Tosa duplex, look up the parcel at https://www.wauwatosa.net/government/departments/administration/property-information, then send a postal letter to the owner of record. Tosa duplex owners skew older and respond better to physical mail.
- **Room option ($700 ceiling)**: SpareRoom Wauwatosa (~21 rooms) is the highest signal; UWM/Marquette boards next; Craigslist `roo` third (noisier).
- **Benchmarks that should change your strategy**:
  - *< 5 hits with fit_score ≥ 7 / week after 3 weeks* → broaden to all of 53208 / 53210, raise the 2BR rent ceiling to $1,800, and post an "ISO" in the UWM + Marquette FB groups.
  - *> 20 hits / week* → tighten to 53213/53226, add an in-unit-laundry / off-street-parking requirement to the Claude scoring prompt.
  - *MCW list returns nothing in 14 days* → also email `apartments@marquette.edu` requesting addition to their off-campus housing announcements; the Marquette list extends west into Tosa.
- **Have a one-page renter packet ready before any alert fires**: most recent credit pull, fellowship/employer letter, two references, and a one-paragraph "about me." September is Milwaukee's tightest leasing month due to Marquette/UWM/MCW move-in; expect to apply within 24 hours of a strong hit.

---

## Caveats

- **Berrada Properties context.** Per Wisconsin Public Radio's December 18, 2024 coverage of AG Josh Kaul's announcement, Berrada agreed to a **$1.7M consent judgment** ending a Wisconsin DOJ lawsuit (filed Nov. 15, 2021); Kaul called it "the largest settlement for a housing case in state history." Berrada operates ~9,000 units across 200+ LLCs in Milwaukee and Racine; per the Wisconsin DATCP FAQ (updated Feb. 28, 2025) the settlement requires Berrada to vacate and seal **~3,250 qualifying eviction judgments** from 2015–2020. Their portfolio is largely north/central Milwaukee complexes, with limited Tosa relevance, but be aware of this history if you encounter their inventory.
- **Wauwatosa has no public rental registry.** Unlike Milwaukee's DNS Property Registration, you cannot pull a bulk Tosa landlord list. Parcel-by-parcel lookups only via https://www.wauwatosa.net/government/departments/administration/property-information.
- **Facebook scraping legal status is unsettled.** Although *Meta v. Bright Data* (N.D. Cal. Jan 2024) actually went against Meta on logged-off public scraping, that ruling does not authorize logged-in automated scraping — keep your Chrome agent manual.
- **September 2026 is the worst-timing month** for Tosa supply due to Marquette/UWM/MCW move-in. Listings for September typically post May–July 2026. Start the pipeline by early summer 2026 at the latest.
- **Craigslist scam density is high.** Verify each listing by (a) reverse-image-searching the photos, (b) cross-checking the address in the Milwaukee/Wauwatosa property search to confirm owner identity, and (c) never wiring deposits before an in-person tour.
- **Rental churn is higher than philosophy-job churn.** Run your scraper every 4–6 hours, not weekly. Expect noisier data than PhilJobs; lean harder on Claude-side filtering.
- **Wauwatosa NOW / OnMilwaukee / Shepherd Express classifieds are dead or never existed.** Don't waste time there.
- **MCW credentialed access is gated** by their housing office; don't try to bypass — just ask politely via mcwhousing@mcw.edu and wait.