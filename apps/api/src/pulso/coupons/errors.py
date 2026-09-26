from pulso.catalog.errors import CatalogError


class CouponInvalidoError(CatalogError):
    """Regra de cupom incoerente (ex. teto em cupom de valor fixo)."""

    status_code = 422
