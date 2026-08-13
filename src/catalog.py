CPUS = (
    {'id': 'ryzen-7-7800x3d', 'name': 'AMD Ryzen 7 7800X3D', 'socket': 'AM5'},
    {'id': 'core-i5-14600k', 'name': 'Intel Core i5-14600K', 'socket': 'LGA1700'},
)

MOTHERBOARDS = (
    {'id': 'msi-b650', 'name': 'MSI B650', 'socket': 'AM5', 'memory_standard': 'DDR5'},
    {'id': 'asus-z790', 'name': 'ASUS Z790', 'socket': 'LGA1700', 'memory_standard': 'DDR5'},
)

MEMORY = (
    {'id': 'corsair-vengeance-ddr5', 'name': 'Corsair Vengeance DDR5', 'standard': 'DDR5'},
    {'id': 'kingston-fury-ddr4', 'name': 'Kingston Fury DDR4', 'standard': 'DDR4'},
)


def find_product(products, product_id):
    return next((product for product in products if product['id'] == product_id), None)
