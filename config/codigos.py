import re


def correlativo(prefijo, identificador, digitos=6):
    """Format the database sequence; padding is a minimum, never a truncation."""
    if identificador is None:
        return None
    if isinstance(identificador, bool) or not isinstance(identificador, int) or identificador < 1:
        raise ValueError('El identificador debe ser un entero positivo.')
    return f'{prefijo}-{identificador:0{digitos}d}'


def identificador_correlativo(prefijo, codigo, digitos=6):
    value = codigo.strip().upper()
    if not re.fullmatch(re.escape(prefijo) + r'-[0-9]{6,19}', value):
        raise ValueError('Codigo correlativo invalido.')
    pk = int(value.split('-', 1)[1])
    if not 1 <= pk <= 9223372036854775807 or correlativo(prefijo, pk, digitos) != value:
        raise ValueError('Codigo correlativo invalido.')
    return pk
