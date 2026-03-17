## Deploy on Render

This app can be deployed as a public FastAPI web service using the included `render.yaml`.

### What you need

- A GitHub repo with the latest `master`
- A Render account
- Your existing `DATABASE_URL` from Supabase

### Steps

1. Push the latest code to GitHub.
2. In Render, choose `New +` -> `Blueprint`.
3. Connect your GitHub repo and select this project.
4. Render will detect `render.yaml` and propose a web service called `botntk-web`.
5. Set the environment variable:
   - `DATABASE_URL` = your Supabase connection string
6. Create the service.

### Start command

Render runs:

```bash
uvicorn api:app --host 0.0.0.0 --port $PORT
```

### Health check

Render checks:

```text
/health
```

### What will be public

- `/` -> dashboard
- `/game?id=...` -> game detail page
- `/docs` -> FastAPI docs

### Notes

- The web app only needs `DATABASE_URL`.
- It does not need the Google Sheets or scraper secrets.
- Your daily GitHub Actions scraper can continue writing into the same Supabase database.
