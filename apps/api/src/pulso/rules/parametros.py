"""Numeros do motor de regras (RF15-RF18), num lugar so. Ajuste aqui, nao na logica."""

from decimal import Decimal

# Faixas pela BASE de comparacao (media ou minimo), nao pelo preco de hoje: (ate, queda minima %).
# A ultima faixa nao tem teto.
FAIXAS_LIMIAR: tuple[tuple[Decimal | None, Decimal], ...] = (
    (Decimal("300.00"), Decimal("20")),
    (Decimal("1500.00"), Decimal("15")),
    (None, Decimal("10")),
)
PISO_ECONOMIA = Decimal("30.00")  # reais; vale para a regra da media e a do minimo

# Guarda de historico ralo (regra da media)
COBERTURA_MINIMA_MEDIA = Decimal("0.5")
AMOSTRAS_MINIMAS_MEDIA = 3

# RF16
JANELA_REPETICAO_HORAS = 24
QUEDA_ADICIONAL_MINIMA = Decimal("5")  # % sobre o preco do candidato anterior
