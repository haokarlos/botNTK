import requests
from bs4 import BeautifulSoup
import csv

# URL de la página que deseas escrapear
url = 'https://www.nutaku.net/games/genre/tag/pc-browser/os/dev/pub/lang/filter/price/features/status/ranking/'

# Realizar una solicitud GET a la URL
response = requests.get(url)

# Verifica si la solicitud fue exitosa
if response.status_code == 200:
    # Crea un objeto BeautifulSoup para analizar el HTML
    soup = BeautifulSoup(response.text, 'html.parser')

    # Encuentra todas las etiquetas <span> con la clase "general-title"
    general_titles = soup.find_all('span', class_='general-title')

    # Abre un archivo CSV en modo escritura
    with open('resultados.csv', 'w', newline='') as csv_file:
        # Crea un objeto CSV para escribir los resultados
        csv_writer = csv.writer(csv_file)
        # Escribe el encabezado del CSV (opcional)
        csv_writer.writerow(['Títulos NTK'])  # Puedes personalizar el encabezado

        # Itera a través de las etiquetas encontradas y escribe el texto en el archivo CSV
        for title in general_titles:
            csv_writer.writerow([title.text])

    print('Los resultados se han guardado en resultados.csv')
else:
    print(f'Error al acceder a la página. Código de estado: {response.status_code}')

# No olvides desactivar el entorno virtual cuando hayas terminado
# deactivate

