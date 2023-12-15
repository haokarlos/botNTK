import requests
from bs4 import BeautifulSoup
from datetime import datetime
import gspread
from google.oauth2 import service_account

# URL de la página web que deseas raspar
url = 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/'

# Genera la fecha de hoy en el formato AAAA-MM-DD
today = datetime.now().strftime('%Y-%m-%d')

# Realiza una solicitud GET a la URL
response = requests.get(url)

# Verifica si la solicitud fue exitosa
if response.status_code == 200:
    # Crea un objeto BeautifulSoup para analizar el HTML
    soup = BeautifulSoup(response.text, 'html.parser')

    # Encuentra todas las etiquetas <span> con la clase "general-title"
    general_titles = [title.text for title in soup.find_all('span', class_='general-title')]

    # Carga las credenciales desde el archivo JSON
    credentials = service_account.Credentials.from_service_account_file('/Users/carlosgarciagonzalez/Documents/BotNTK/topgamesntk-bef66ad4669f.json', scopes=['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive'])

    # Autentica con Google Sheets
    gc = gspread.authorize(credentials)

    # Abre la hoja de cálculo por su título o URL
    sheet = gc.open('EF Nutaku top games bot')

    # Selecciona la primera hoja de la hoja de cálculo
    worksheet = sheet.get_worksheet(0)

    # Agrega la fecha y los títulos a la hoja de cálculo
    data = [today] + general_titles
    worksheet.append_row(data)

    print('Los resultados se han guardado en Google Sheets')
else:
    print(f'Error al acceder a la página. Código de estado: {response.status_code}')
