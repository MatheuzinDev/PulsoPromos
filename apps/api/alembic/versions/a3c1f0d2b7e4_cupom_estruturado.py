"""cupom estruturado: tipo, valor, minimo_compra, teto_desconto, loja (RF13)

Revision ID: a3c1f0d2b7e4
Revises: 697b919c6a1f
Create Date: 2026-09-26 12:00:00

`regra_texto` fica, agora como observacao humana. Nao ha cupom em producao: `tipo` e `valor`
entram NOT NULL com default provisorio so para a tabela aceitar linhas antigas, e o default
e removido em seguida.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c1f0d2b7e4"
down_revision: str | None = "697b919c6a1f"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coupon",
        sa.Column("tipo", sa.String(length=20), nullable=False, server_default="valor_fixo"),
    )
    op.add_column(
        "coupon",
        sa.Column(
            "valor", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.01"
        ),
    )
    op.alter_column("coupon", "tipo", server_default=None)
    op.alter_column("coupon", "valor", server_default=None)
    op.add_column(
        "coupon", sa.Column("minimo_compra", sa.Numeric(precision=12, scale=2), nullable=True)
    )
    op.add_column(
        "coupon", sa.Column("teto_desconto", sa.Numeric(precision=12, scale=2), nullable=True)
    )
    op.add_column("coupon", sa.Column("loja", sa.String(length=60), nullable=True))
    op.create_check_constraint("ck_coupon_tipo", "coupon", "tipo IN ('percentual', 'valor_fixo')")
    op.create_check_constraint(
        "ck_coupon_valor",
        "coupon",
        "(tipo = 'percentual' AND valor > 0 AND valor <= 100)"
        " OR (tipo = 'valor_fixo' AND valor > 0)",
    )
    op.create_check_constraint(
        "ck_coupon_teto_so_percentual",
        "coupon",
        "teto_desconto IS NULL OR (tipo = 'percentual' AND teto_desconto > 0)",
    )
    op.create_check_constraint(
        "ck_coupon_minimo_compra", "coupon", "minimo_compra IS NULL OR minimo_compra > 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_coupon_minimo_compra", "coupon", type_="check")
    op.drop_constraint("ck_coupon_teto_so_percentual", "coupon", type_="check")
    op.drop_constraint("ck_coupon_valor", "coupon", type_="check")
    op.drop_constraint("ck_coupon_tipo", "coupon", type_="check")
    op.drop_column("coupon", "loja")
    op.drop_column("coupon", "teto_desconto")
    op.drop_column("coupon", "minimo_compra")
    op.drop_column("coupon", "valor")
    op.drop_column("coupon", "tipo")
