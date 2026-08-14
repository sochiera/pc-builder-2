from src.catalog import find_product


RAM_ANALYSIS_REQUIRED_MESSAGE = 'Wybierz plyte glowna i pamiec RAM, aby sprawdzic zgodnosc.'
POWER_ANALYSIS_REQUIRED_MESSAGE = 'Wybierz procesor, plyte glowna, pamiec RAM i zasilacz, aby sprawdzic moc.'
INITIAL_ANALYSIS_REQUIRED_MESSAGE = (
    f'{POWER_ANALYSIS_REQUIRED_MESSAGE} '
    'Wybierz plyte glowna i obudowe, aby sprawdzic format.'
)


def combine_analyses(*analyses):
    levels = [analysis['level'] for analysis in analyses]
    if 'blocking' in levels:
        level = 'blocking'
    elif 'info' in levels:
        level = 'info'
    else:
        level = 'ok'
    return {
        'level': level,
        'message': ' '.join(analysis['message'] for analysis in analyses),
    }


def total_cost(part_ids, catalogs):
    return sum(
        product['price_pln']
        for product_id, catalog in zip(part_ids, catalogs)
        for product in (find_product(catalog, product_id),)
        if product is not None
    )


def analyze_budget(budget_pln, build_cost):
    if not budget_pln or not budget_pln.isascii() or not budget_pln.isdigit():
        return {
            'level': 'info',
            'message': 'Podaj budzet jako nieujemna calkowita kwote w PLN.',
        }

    try:
        budget = int(budget_pln)
    except ValueError:
        return {
            'level': 'info',
            'message': 'Podaj budzet jako nieujemna calkowita kwote w PLN.',
        }
    if budget >= build_cost:
        remaining = budget - build_cost
        return {
            'level': 'ok',
            'message': f'Zestaw miesci sie w budzecie oraz pozostaje {remaining} PLN.',
            'remaining_pln': remaining,
        }

    return {
        'level': 'blocking',
        'message': f'Budzet jest przekroczony o {build_cost - budget} PLN.',
        'overage_pln': build_cost - budget,
    }


def analyze_build(cpu_socket, motherboard_socket):
    if not cpu_socket or not motherboard_socket:
        return {
            'level': 'info',
            'message': 'Wybierz procesor i plyte glowna, aby sprawdzic socket.',
        }

    if cpu_socket != motherboard_socket:
        return {
            'level': 'blocking',
            'message': f'Procesor wymaga socketu {cpu_socket}, a plyta ma {motherboard_socket}.',
        }

    return {
        'level': 'ok',
        'message': f'Socket {cpu_socket} procesora i plyty glownej jest zgodny.',
    }


def analyze_products(cpu_id, motherboard_id, cpus, motherboards):
    if not cpu_id or not motherboard_id:
        return analyze_build('', '')

    cpu = find_product(cpus, cpu_id)
    motherboard = find_product(motherboards, motherboard_id)
    if not cpu or not motherboard:
        return {
            'level': 'info',
            'message': 'Wybierz procesor i plyte glowna, aby sprawdzic socket.',
        }

    return analyze_build(cpu['socket'], motherboard['socket'])


def analyze_memory(motherboard_id, ram_id, motherboards, memories):
    if not motherboard_id or not ram_id:
        return {
            'level': 'info',
            'message': RAM_ANALYSIS_REQUIRED_MESSAGE,
        }

    motherboard = find_product(motherboards, motherboard_id)
    memory = find_product(memories, ram_id)
    if not motherboard or not memory:
        return {
            'level': 'info',
            'message': 'Wybierz znana plyte glowna i pamiec RAM, aby sprawdzic zgodnosc.',
        }

    motherboard_standard = motherboard.get('memory_standard')
    memory_standard = memory['standard']
    if motherboard_standard != memory_standard:
        return {
            'level': 'blocking',
            'message': (
                f'Pamiec RAM {memory_standard} jest niezgodna; '
                f'plyta obsluguje {motherboard_standard}.'
            ),
        }

    return {
        'level': 'ok',
        'message': f'Pamiec RAM {memory_standard} jest zgodna z plyta glowna.',
    }


def analyze_case(motherboard_id, case_id, motherboards, cases):
    if not motherboard_id or not case_id:
        return {
            'level': 'info',
            'message': 'Wybierz plyte glowna i obudowe, aby sprawdzic format.',
        }

    motherboard = find_product(motherboards, motherboard_id)
    case = find_product(cases, case_id)
    if not motherboard or not case:
        return {
            'level': 'info',
            'message': 'Wybierz znana plyte glowna i obudowe, aby sprawdzic format.',
        }

    form_factor = motherboard['form_factor']
    supported_form_factors = case['supported_form_factors']
    if form_factor not in supported_form_factors:
        return {
            'level': 'blocking',
            'message': (
                f'Plyta w formacie {form_factor} nie pasuje do obudowy '
                f'obslugujacej formaty: {", ".join(supported_form_factors)}.'
            ),
        }

    return {
        'level': 'ok',
        'message': f'Plyta w formacie {form_factor} pasuje do obudowy.',
    }


def analyze_power_supply(cpu_id, motherboard_id, ram_id, psu_id, cpus, motherboards, memories, power_supplies):
    if not all((cpu_id, motherboard_id, ram_id, psu_id)):
        return {
            'level': 'info',
            'message': POWER_ANALYSIS_REQUIRED_MESSAGE,
        }

    products = (
        find_product(cpus, cpu_id),
        find_product(motherboards, motherboard_id),
        find_product(memories, ram_id),
        find_product(power_supplies, psu_id),
    )
    if any(product is None for product in products):
        return {
            'level': 'info',
            'message': 'Wybierz znane czesci i zasilacz, aby sprawdzic moc.',
        }

    cpu, motherboard, memory, power_supply = products
    required = sum(product['power_watts'] for product in (cpu, motherboard, memory))
    available = power_supply['power_watts']
    if available < required:
        return {
            'level': 'blocking',
            'message': f'Zestaw wymaga {required} W, a zasilacz dostarcza {available} W.',
        }

    return {
        'level': 'ok',
        'message': f'Moc zasilacza {available} W jest wystarczajaca; zestaw wymaga {required} W.',
    }
