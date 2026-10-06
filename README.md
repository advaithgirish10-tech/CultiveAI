# Cultive AI

The intelligence and automation layer for polyhouses and nurseries.

| Folder | What it is |
|---|---|
| `edge/` | Raspberry Pi service: reads RS485 sensors, stores data offline, runs relays with safety limits. See `edge/README.md` |
| `cloud/` | (next) Supabase schema, ingest + command functions |
| `dashboard/` | (next) web dashboard |

Secrets never go in git: use `.env` files (ignored).
