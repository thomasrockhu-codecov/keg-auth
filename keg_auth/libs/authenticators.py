import flask_login

try:
    import flask_jwt_extended
except ImportError:
    pass


class UserNotFound(Exception):
    pass


class UserInactive(Exception):
    def __init__(self, user):
        self.user = user


class UserInvalidAuth(Exception):
    def __init__(self, user):
        self.user = user


class Authenticator(object):
    """ Generic authenticator interface for determining if a user is logged in.

        Most authenticators will rely on flask-login's current user. Token authenticators
        will need to validate here and bring a user into that context. """
    authentication_failure_redirect = True

    def __init__(self, app):
        self.user_ent = app.auth_manager.get_user_entity()

    @staticmethod
    def get_authenticated_user():
        if flask_login.current_user.is_authenticated:
            return flask_login.current_user

    @classmethod
    def get_identifier(cls):
        return cls.__name__.lower().replace('authenticator', '')


class PasswordAuthenticatorMixin(object):
    """ Username/password authenticators will need a way to verify a user is valid
        prior to making it the current user in flask login """
    def verify_user(self, login_id=None, password=None):
        raise Exception('fill in get_user method')  # pragma: no cover

    def verify_password(self, user, password):
        return user.password == password


class TokenAuthenticatorMixin(object):
    """ Token authenticators will need a way to generate an access token, which will then be
        loaded in the request to log a user into flask-login """
    authentication_failure_redirect = False

    def create_access_token(self, user):
        raise Exception('fill in create_access_token method')  # pragma: no cover


class KegAuthenticator(PasswordAuthenticatorMixin, Authenticator):
    def verify_user(self, login_id=None, password=None):
        user = self.user_ent.query.filter_by(email=login_id).one_or_none()

        if not user:
            raise UserNotFound
        if not user.is_active:
            raise UserInactive(user)
        if password and not self.verify_password(user, password):
            raise UserInvalidAuth(user)

        return user

    def verify_password(self, user, password):
        return user.password == password


class JwtAuthenticator(TokenAuthenticatorMixin, Authenticator):
    """ Authenticator for JWT tokens contained in the Authorization header.

        Requires flask-jwt-extended (`pip install keg-auth[jwt]`)"""
    def __init__(self, app):
        super(JwtAuthenticator, self).__init__(app)

        self.jwt_manager = jwt_manager = flask_jwt_extended.JWTManager()
        jwt_manager.init_app(app)

        @jwt_manager.user_identity_loader
        def user_identity_loader(user):
            """
            Serialize a user entity to the JWT token
            This method is the complement of `user_loader_callback_loader`
            """
            return user.session_key

        @jwt_manager.user_loader_callback_loader
        def user_loader_callback_loader(session_key):
            """
            Load a user entity from the JWT token
            This method is the complement of `user_identity_loader`

            Note, if user is not found or inactive, fail silently - user just won't get loaded
            """
            return self.user_ent.get_by(session_key=session_key, is_active=True)

    @staticmethod
    def get_authenticated_user():
        try:
            flask_jwt_extended.verify_jwt_in_request()
            user = flask_jwt_extended.get_current_user()
            flask_login.login_user(user)
            return user
        except flask_jwt_extended.exceptions.JWTExtendedException:
            return None

    def create_access_token(self, user):
        return flask_jwt_extended.create_access_token(user)
