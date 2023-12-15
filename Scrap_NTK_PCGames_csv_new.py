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
if not os.path.isfile('daily_NTK_Games.csv'):
    # Si no existe, crea un nuevo archivo con la fecha de hoy como encabezado
    with open('daily_NTK_Games.csv', 'w', newline='') as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow([today])

# Abre el archivo CSV en modo lectura para verificar si la fecha de hoy ya ha sido registrada
with open('daily_NTK_Games.csv', 'r', newline='') as csv_file:
    csv_reader = csv.reader(csv_file)
    rows = list(csv_reader)
    headers = rows[0] if rows else []

# Verifica si la fecha de hoy ya está registrada
if today not in headers:
    # Si no está registrada, agrega la fecha de hoy como encabezado
    headers.append(today)

    # Abre el archivo CSV en modo escritura y escribe los nuevos encabezados
    with open('daily_NTK_Games.csv', 'w', newline='') as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(headers)

# Realiza una solicitud GET a la URL
response = requests.get(url)

# Verifica si la solicitud fue exitosa
if response.status_code == 200:
    # Crea un objeto BeautifulSoup para analizar el HTML
    soup = BeautifulSoup(response.text, 'html.parser')

    # Encuentra todas las etiquetas <span> con la clase "general-title"
    general_titles = [title.text for title in soup.find_all('span', class_='general-title')]

    # Abre el archivo CSV en modo lectura para obtener los datos existentes
    with open('daily_NTK_Games.csv', 'r', newline='') as csv_file:
        csv_reader = csv.reader(csv_file)
        rows = list(csv_reader)

    # Asegura que haya una fila para cada título en la nueva columna
    while len(general_titles) > len(rows) - 1:
        rows.append([''] * len(headers))

    # Agrega la fecha y los títulos en la nueva columna
    for i, title in enumerate(general_titles):
        rows[i + 1].append(title)

    # Abre el archivo CSV en modo escritura y escribe los datos actualizados
    with open('daily_NTK_Games.csv', 'w', newline='') as csv_file:
        csv_writer = csv.writer(csv_file)
        csv_writer.writerows(rows)

    print('Los daily_NTK_Games se han guardado en daily_NTK_Games.csv')
else:
    print(f'Error al acceder a la página. Código de estado: {response.status_code}')
