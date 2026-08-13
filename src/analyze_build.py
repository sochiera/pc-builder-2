from src.catalog import find_product


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
