import requests
from bs4 import BeautifulSoup
import gspread
from google.auth import load_credentials_from_file

# URL de la página web que deseas raspar
url = 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/'

# Realiza una solicitud GET a la URL
response = requests.get(url, verify=False)

if response.status_code == 200:
    # Crea un objeto BeautifulSoup para analizar el HTML
    soup = BeautifulSoup(response.text, 'html.parser')

    # Encuentra todas las etiquetas <span> con la clase "general-title"
    general_titles = soup.find_all('span', class_='general-title')

    # Configura la autenticación utilizando google-auth
    credentials = load_credentials_from_file('topgamesntk-bef66ad4669f.json', scopes=['https://www.googleapis.com/auth/spreadsheets'])

    # Abre tu archivo de Google Sheets (reemplaza 'Nombre_del_Archivo' por el nombre de tu archivo)
    gc = gspread.Client(auth=credentials)
    sh = gc.open('EF Nutaku top games bot')

    # Selecciona la hoja de cálculo en la que deseas guardar los datos (reemplaza 'Hoja 1' por el nombre de tu hoja)
    worksheet = sh.worksheet('Hoja 1')

    # Itera a través de las etiquetas encontradas y guarda el texto en Google Sheets
    for i, title in enumerate(general_titles, start=1):
        text = title.text
        # Escribe el texto en la hoja de cálculo en la columna A, fila i
        worksheet.update_cell(i, 1, text)
else:
    print(f'Error al acceder a la página. Código de estado: {response.status_code}')

# No olvides desactivar el entorno virtual cuando hayas terminado
# deactivate
