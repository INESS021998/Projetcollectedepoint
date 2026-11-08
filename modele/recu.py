from settings import db, app


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(20),
        unique=True,
        nullable=False
    )
    password_hash = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<User {self.id} {self.username}>'


class Recu(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    montant = db.Column(db.Numeric(10, 2), nullable=False)
    date_recu = db.Column(db.DateTime, nullable=False)
    magasin = db.Column(db.String(150), nullable=False)
    points = db.Column(db.Integer, nullable=False)
    photo = db.Column(db.String(255))
    user_id = db.Column(
        db.Integer,
        db.ForeignKey(User.__table__.c.id),
        nullable=False
    )
class EchangeMagasin(db.Model):
    # La table existante conserve l’historique des échanges en magasin.
    __tablename__ = "echange"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey(User.__table__.c.id),
        nullable=False
    )
    points_utilises = db.Column(db.Integer, nullable=False)
    date_echange = db.Column(db.DateTime, nullable=False)
    # Nullable to preserve existing exchange history.
    magasin = db.Column(db.String(150), nullable=True)
    
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
