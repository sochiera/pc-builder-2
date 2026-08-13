CPUS = (
    {
        'id': 'ryzen-7-7800x3d',
        'name': 'AMD Ryzen 7 7800X3D',
        'socket': 'AM5',
        'power_watts': 120,
    },
    {
        'id': 'core-i5-14600k',
        'name': 'Intel Core i5-14600K',
        'socket': 'LGA1700',
        'power_watts': 125,
    },
)

MOTHERBOARDS = (
    {
        'id': 'msi-b650',
        'name': 'MSI B650',
        'socket': 'AM5',
        'memory_standard': 'DDR5',
        'form_factor': 'ATX',
        'power_watts': 80,
    },
    {
        'id': 'asus-z790',
        'name': 'ASUS Z790',
        'socket': 'LGA1700',
        'memory_standard': 'DDR5',
        'form_factor': 'ATX',
        'power_watts': 70,
    },
)

MEMORY = (
    {
        'id': 'corsair-vengeance-ddr5',
        'name': 'Corsair Vengeance DDR5',
        'standard': 'DDR5',
        'power_watts': 10,
    },
    {
        'id': 'kingston-fury-ddr4',
        'name': 'Kingston Fury DDR4',
        'standard': 'DDR4',
        'power_watts': 8,
    },
)

POWER_SUPPLIES = (
    {'id': 'corsair-rm750x', 'name': 'Corsair RM750x', 'power_watts': 750},
    {'id': 'be-quiet-pure-power-12-m-850w', 'name': 'be quiet! Pure Power 12 M 850W', 'power_watts': 850},
)

CASES = (
    {
        'id': 'atx-mid-tower',
        'name': 'ATX Mid Tower',
        'supported_form_factors': ('ATX', 'Micro-ATX', 'Mini-ITX'),
    },
    {
        'id': 'mini-itx-compact',
        'name': 'Mini-ITX Compact',
        'supported_form_factors': ('Mini-ITX',),
    },
)


def find_product(products, product_id):
    return next((product for product in products if product['id'] == product_id), None)
