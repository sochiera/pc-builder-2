from src.catalog import find_product


RAM_ANALYSIS_REQUIRED_MESSAGE = 'Wybierz plyte glowna i pamiec RAM, aby sprawdzic zgodnosc.'
POWER_ANALYSIS_REQUIRED_MESSAGE = 'Wybierz procesor, plyte glowna, pamiec RAM i zasilacz, aby sprawdzic moc.'


def combine_analyses(*analyses):
    levels = [analysis['level'] for analysis in analyses]
    if 'info' in levels:
        level = 'info'
    elif 'blocking' in levels:
        level = 'blocking'
    else:
        level = 'ok'
    return {
        'level': level,
        'message': ' '.join(analysis['message'] for analysis in analyses),
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
