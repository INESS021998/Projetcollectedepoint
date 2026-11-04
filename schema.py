from decimal import Decimal, localcontext

from marshmallow import (
    Schema,
    fields,
    validate,
    pre_load,
    ValidationError
)


def deux_decimales(valeur):
    # Adapter la précision pour éviter une erreur sur les grands nombres
    with localcontext() as contexte:
        contexte.prec = max(
            28,
            len(valeur.as_tuple().digits),
            valeur.adjusted() + 3
        )
        # Vérifier sans arrondir les montants incorrects
        if valeur != valeur.quantize(Decimal("0.01")):
            raise ValidationError(
                "Le montant doit avoir au maximum deux décimales."
            )
class RecuCreationSchema(Schema):
    montant = fields.Decimal(
        required=True,
        allow_nan=False,
        validate=[
            validate.Range(
                min=Decimal("0"),
                min_inclusive=False,
                error="Le montant doit être supérieur à zéro."
            ),
            deux_decimales
        ]
    )
    date_recu = fields.Date(
        required=True,
        format="%Y-%m-%d"
    )
    magasin = fields.String(
        required=True,
        validate=validate.Length(min=1, max=150)
    )
    photo= fields.String(
        required=True,
        validate=validate.Length(max=255)
    )

    @pre_load
    def netoyage_texte(self, donnees, **kwargs):
        if not isinstance(donnees, dict):
            raise ValidationError(
                "Le contenu doit être un objet JSON valide."
            )

        donnees = donnees.copy()
        # Retirer les espaces autour du nom du magasin
        if isinstance(donnees.get("magasin"), str):
            donnees["magasin"] = donnees["magasin"].strip()

        # Conserver le format strict AAAA-MM-JJ
        valeur_date = donnees.get("date_recu")

        if isinstance(valeur_date, str):
            import re
            if not re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}",
                valeur_date
            ):
                raise ValidationError({
                    "date_recu": [
                        "La date doit respecter le format AAAA-MM-JJ."
                    ]
                })
        return donnees

recu_schema = RecuCreationSchema()