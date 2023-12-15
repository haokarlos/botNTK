import requests
from bs4 import BeautifulSoup
import csv
from datetime import datetime
import os

# URL de la página web que deseas raspar
url = 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/'

# Genera la fecha de hoy en el formato AAAA-MM-DD
today = datetime.now().strftime('%Y-%m-%d')

# Verifica si el archivo CSV existe
if not os.path.isfile('resultados.csv'):
    # Si no existe, crea un nuevo archivo con los encabezados
    with open('resultados.csv', 'w', newline='') as csv_file:
        csv_writer = csv.writer(csv_file)

# Abre el archivo CSV en modo lectura para verificar la última fecha registrada
with open('resultados.csv', 'r', newline='') as csv_file:
    csv_reader = csv.reader(csv_file)
    rows = list(csv_reader)
    dates = [row[0] for row in rows]

# Verifica si la fecha de hoy ya está registrada
if today not in dates:
    # Realiza una solicitud GET a la URL
    response = requests.get(url)

    # Verifica si la solicitud fue exitosa
    if response.status_code == 200:
        # Crea un objeto BeautifulSoup para analizar el HTML
        soup = BeautifulSoup(response.text, 'html.parser')

        # Encuentra todas las etiquetas <span> con la clase "general-title"
        general_titles = [title.text for title in soup.find_all('span', class_='general-title')]

        # Abre el archivo CSV en modo escritura y escribe la fecha en la primera columna y los títulos en las siguientes columnas de la misma fila
        with open('resultados.csv', 'a', newline='') as csv_file:
            csv_writer = csv.writer(csv_file)
            csv_writer.writerow([today] + general_titles)

        print('Los resultados se han guardado en resultados.csv')
    else:
        print(f'Error al acceder a la página. Código de estado: {response.status_code}')
else:
    print('Hoy ya se ha registrado. No se han agregado nuevos datos.')
