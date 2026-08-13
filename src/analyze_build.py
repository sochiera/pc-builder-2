from src.catalog import find_product


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
            'message': 'Wybierz plyte glowna i pamiec RAM, aby sprawdzic zgodnosc.',
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
