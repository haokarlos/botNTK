import requests
from bs4 import BeautifulSoup
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from google.oauth2 import service_account
import gspread
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Función para obtener los nombres de los juegos de Nutaku
def get_nutaku_top_game_names(nutaku_url):
    response = requests.get(nutaku_url)
    
    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        general_titles = [title.text for title in soup.find_all('span', class_='general-title')]
        return general_titles
    else:
        print(f'Error al acceder a la página de Nutaku. Código de estado: {response.status_code}')
        return []

# Función para obtener los nombres de los juegos de Ero-Labs
def get_ero_labs_top_game_names():
    ero_labs_url = 'https://www.ero-labs.com/en/'

    # Configuración para ejecutar Chrome en segundo plano (headless)
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")

    # Configuración del controlador de Chrome
    driver = webdriver.Chrome(options=chrome_options)  # Asegúrate de tener el controlador de Chrome instalado y en el PATH
    driver.get(ero_labs_url)

    # Espera a que la página se cargue completamente
    wait = WebDriverWait(driver, 20)  # Aumenté el tiempo de espera a 20 segundos
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '.home__topGameName')))  # Cambié a 'presence_of_element_located'

    # Obtiene el contenido HTML de la página después de que se ha ejecutado JavaScript
    page_source = driver.page_source

    # Parsea el contenido HTML con BeautifulSoup
    soup = BeautifulSoup(page_source, 'html.parser')

    # Encuentra todas las etiquetas <h4> dentro de las clases 'home__topGameName'
    game_name_elements = soup.select('.home__topGameName h4')

    ero_labs_top_game_names = [element.get_text(strip=True) for element in game_name_elements]

    driver.quit()
    return ero_labs_top_game_names

# Función para escribir los nombres de los juegos en Google Sheets
def write_to_google_sheets(sheet, game_names, sheet_index):
    today = datetime.now().strftime('%Y-%m-%d')

    # Escribe en la hoja correspondiente
    worksheet = sheet.get_worksheet(sheet_index)
    data = [today] + game_names
    worksheet.append_row(data)

    print(f'Los resultados se han guardado en la hoja {sheet_index} de Google Sheets')

# URLs y números de hoja correspondientes a cada consulta de Nutaku
nutaku_queries = [
    ('https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/', 0),
    ('https://www.nutaku.net/games/genre/tag/mobile/os/dev/pub/lang/filter/price/features/status/ranking/', 1),
    ('https://www.nutaku.net/games/', 2)
]

# Definir sheet fuera del bucle for
credentials = service_account.Credentials.from_service_account_file('/Users/carlosgarciagonzalez/Documents/BotNTK/topgamesntk-bef66ad4669f.json', 
                                                                     scopes=['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive'])
gc = gspread.authorize(credentials)
sheet = gc.open('EF Nutaku top games bot')

# Realiza las consultas de Nutaku y guarda los resultados en hojas diferentes
for query_url, sheet_index in nutaku_queries:
    nutaku_top_game_names = get_nutaku_top_game_names(query_url)
    write_to_google_sheets(sheet, nutaku_top_game_names, sheet_index)

# Obtén los nombres de los juegos de Ero-Labs
ero_labs_top_game_names = get_ero_labs_top_game_names()

# Ajusta la lista de juegos de Ero-Labs para contener solo los primeros 19
ero_labs_top_game_names = ero_labs_top_game_names[:19]

# Escribe los nombres en la cuarta hoja del documento de Google Sheets
write_to_google_sheets(sheet, ero_labs_top_game_names, 3)
