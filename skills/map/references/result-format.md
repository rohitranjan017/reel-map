# Place extraction result

The interpreting model writes this structure after inspecting the actual source:

```json
{
  "title": "London places from a Reel",
  "source_url": "https://www.instagram.com/reel/DbyySxJuluW/",
  "places": [
    {
      "name": "British Museum",
      "address": "London, United Kingdom",
      "evidence": "On-screen label at approximately 2 seconds",
      "google_place_id": "",
      "place_id_source": ""
    }
  ]
}
```

`name` is required. `address` adds city, country, or an externally confirmed street address. `evidence` is strongly recommended. `google_place_id` is optional and must be sourced; a nonempty ID also requires an HTTPS `place_id_source`. Omit unknown values instead of inventing them.

The link helper makes no web requests, performs no inference or geocoding, and does not verify whether Google resolves a name or ID correctly. Use an exact establishment and address when supported by evidence. Routes are generated in input order. URLs are not shortened and do not save anything to the user's account.

Google's Maps URLs documentation defines the formats and limits:
https://developers.google.com/maps/documentation/urls/get-started

Shortlist links support up to 20 Place IDs. This helper conservatively caps routes at 10 stops, and mobile-browser routes at 5 stops (origin + 3 waypoints + destination). Longer inputs are split with an overlapping endpoint so every place is retained.
