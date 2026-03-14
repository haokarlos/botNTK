# GitHub Actions setup

1. Sube este repositorio a GitHub.
2. En `Settings > Secrets and variables > Actions`, crea estos secrets:
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: pega el contenido completo del JSON de la service account.
   - `GOOGLE_SHEET_NAME`: `EF Nutaku top games bot`
3. En `Actions`, ejecuta manualmente `EL Games Sheet` una vez con `Run workflow`.
4. Si todo va bien, quedará programado cada día a las `08:00 UTC`.

Si quieres otra hora, cambia el cron en [.github/workflows/el-games-sheet.yml](/Users/carlosgarciagonzalez/Documents/BotNTK/.github/workflows/el-games-sheet.yml).
