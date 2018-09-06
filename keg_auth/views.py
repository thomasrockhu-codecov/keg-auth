import flask
import flask_login
import inflect
import keg.web
import sqlalchemy as sa
from blazeutils.strings import case_cw2dash
from keg.db import db

from keg_auth import forms, grids, requires_permissions


class CrudView(keg.web.BaseView):
    grid_cls = None
    form_cls = None
    orm_cls = None
    form_template = 'keg_auth/crud-addedit.html'
    grid_template = 'keg_auth/crud-list.html'
    object_name = None
    _inflect = inflect.engine()
    permissions = {
        'add': None,
        'edit': None,
        'delete': None,
        'list': None
    }

    @classmethod
    def map_method_route(cls, method_name, route, methods):
        method_route = keg.web.MethodRoute(method_name, route, {'methods': methods},
                                           cls.calc_url(), cls.calc_endpoint())
        mr_options = method_route.options()
        view_func = cls.as_view(method_route.view_func_name,
                                method_route.sanitized_method_name('_'))
        cls.view_funcs[method_route.endpoint] = view_func
        mr_options['view_func'] = cls.view_funcs[method_route.endpoint]
        cls.blueprint.add_url_rule(method_route.rule(), **mr_options)

    @classmethod
    def init_routes(cls):
        """ Creates the standard set of routes from methods (add, edit, delete, list).

            To extend to further action routes:
                `cls.map_method_route(method_name, url, HTTP methods)`
                ex. `cls.map_method_route('read', '/foo', ('GET', ))`"""
        super(CrudView, cls).init_routes()

        cls.map_method_route('add', '{}/add'.format(cls.calc_url()), ('GET', 'POST'))
        cls.map_method_route('edit', '{}/<int:objid>/edit'.format(cls.calc_url()), ('GET', 'POST'))
        cls.map_method_route('delete', '{}/<int:objid>/delete'.format(cls.calc_url()), ('GET', ))
        cls.map_method_route('list', '{}'.format(cls.calc_url()), ('GET', 'POST'))

    def __init__(self, *args, **kwargs):
        super(CrudView, self).__init__(*args, **kwargs)
        self.objinst = None

    @property
    def object_name_plural(self):
        return self._inflect.plural(self.object_name)

    def page_title(self, action):
        if action in ('Create', 'Edit'):
            return '{} {}'.format(action, self.object_name)
        return self.object_name_plural

    def create_form(self, obj):
        return self.form_cls(obj=obj)

    def render_form(self, obj, action, form, action_button_text='Save Changes'):
        default_template_args = {
            'action': action,
            'action_button_text': action_button_text,
            'cancel_url': self.cancel_url(),
            'form': form,
            'obj_inst': obj,
            'page_title': self.page_title(action),
        }
        return flask.render_template(self.form_template, **default_template_args)

    def add_orm_obj(self):
        o = self.orm_cls()
        db.session.add(o)
        return o

    def update_obj(self, obj, form):
        obj = obj or self.add_orm_obj()
        form.populate_obj(obj)
        return obj

    def add_edit(self, meth, obj=None):
        form = self.create_form(obj)
        if meth == 'POST':
            if form.validate():
                result = self.update_obj(obj, form)
                db.session.commit()
                if result:
                    return self.on_add_edit_success(result, obj is not None)
            else:
                self.on_add_edit_failure(obj, obj is not None)

        return self.render_form(
            obj=obj,
            action='Edit' if obj else 'Create',
            action_button_text='Save Changes' if obj else 'Create ' + self.object_name,
            form=form
        )

    def init_object(self, obj_id):
        if obj_id is None:
            flask.abort(400)
        self.objinst = self.orm_cls.query.get(obj_id)
        if not self.objinst:
            flask.abort(404)
        return self.objinst

    def add(self):
        return requires_permissions(self.permissions['add'])(self.add_edit)(flask.request.method)

    def edit(self, objid):
        obj = self.init_object(objid)
        return requires_permissions(self.permissions['edit'])(self.add_edit)(
            flask.request.method, obj)

    def delete(self, objid):
        self.init_object(objid)

        def action():
            try:
                self.orm_cls.delete(objid)
            except sa.exc.IntegrityError:
                return self.on_delete_failure()

            return self.on_delete_success()

        return requires_permissions(self.permissions['delete'])(action)()

    def list(self):
        return requires_permissions(self.permissions['list'])(self.render_grid)()

    @property
    def list_url_with_session(self):
        return flask.url_for(self.endpoint_for_action('list'),
                             session_key=flask.request.args.get('session_key'))

    def flash_success(self, verb):
        flask.flash('Successfully {verb} {object}'.format(verb=verb, object=self.object_name),
                    'success')

    def on_delete_success(self):
        self.flash_success('removed')
        return flask.redirect(self.list_url_with_session)

    def on_delete_failure(self):
        flask.flash(
            'Unable to delete {}. It may be referenced by other items.'.format(self.object_name),
            'warning'
        )
        return flask.redirect(self.list_url_with_session)

    def on_add_edit_success(self, entity, is_edit):
        self.flash_success('modified' if is_edit else 'created')
        return flask.redirect(self.list_url_with_session)

    def on_add_edit_failure(self, entity, is_edit):
        flask.flash('Form errors detected.  Please see below for details.', 'error')

    @classmethod
    def endpoint_for_action(cls, action):
        return '{}.{}:{}'.format(cls.blueprint.name, cls.calc_endpoint(), case_cw2dash(action))

    def make_grid(self):
        grid = self.grid_cls()
        grid.apply_qs_args()
        return grid

    def render_grid(self):
        grid = self.make_grid()

        if grid.export_to:
            return grid.export_as_response()

        return flask.render_template(
            self.grid_template,
            add_url=flask.url_for(self.endpoint_for_action('add'),
                                  session_key=grid.session_key),
            page_title=self.page_title('list'),
            grid=grid
        )

    def cancel_url(self):
        return self.list_url_with_session


class AuthRespondedView(keg.web.BaseView):
    """ Base for views which will refer out to the login authenticator for responders

        URL gets calculated from the responder class and must be a class attribute there.

        Note: if the login authenticator doesn't have the referenced key, the view will 404.
    """
    responder_key = None
    auth_manager = None

    def __init__(self):
        super(AuthRespondedView, self).__init__()
        self.responding_method = 'responder'

    @classmethod
    def calc_url(cls):
        authenticator_cls = cls.auth_manager.login_authenticator_cls
        responder_cls = authenticator_cls.responder_cls.get(cls.responder_key)
        return getattr(responder_cls, 'url', None)

    def on_missing_responder(self):
        flask.abort(404)

    def responder(self, *args, **kwargs):
        authenticator = flask.current_app.auth_manager.login_authenticator
        responder = authenticator.get_responder(self.responder_key)

        if not responder:
            self.on_missing_responder()

        return responder(*args, **kwargs)

    def get(self):
        # needed in keg to set up a GET route
        pass

    def post(self):
        # needed in keg to set up a POST route
        pass


class Login(AuthRespondedView):
    responder_key = 'login'


class ForgotPassword(AuthRespondedView):
    responder_key = 'forgot-password'


class ResetPassword(AuthRespondedView):
    responder_key = 'reset-password'


class VerifyAccount(AuthRespondedView):
    responder_key = 'verify-account'


class Logout(keg.web.BaseView):
    url = '/logout'
    flash_success = 'You have been logged out.', 'success'

    def get(self):
        flask_login.logout_user()
        flask.flash(*self.flash_success)
        redirect_to = flask.current_app.auth_manager.url_for('after-logout')
        flask.abort(flask.redirect(redirect_to))


@requires_permissions('auth-manage')
class User(CrudView):
    url = '/users'
    object_name = 'User'
    form_cls = staticmethod(forms.user_form)

    def create_form(self, obj):
        form_cls = self.form_cls(flask.current_app.config,
                                 allow_superuser=flask_login.current_user.is_superuser,
                                 endpoint=self.endpoint_for_action('edit'))
        return form_cls(obj=obj)

    @property
    def orm_cls(self):
        return flask.current_app.auth_manager.entity_registry.user_cls

    @property
    def grid_cls(self):
        return grids.make_user_grid(
            edit_endpoint=self.endpoint_for_action('edit'),
            edit_permission=self.permissions['edit'],
            delete_endpoint=self.endpoint_for_action('delete'),
            delete_permission=self.permissions['delete']
        )

    def create_user(self, form):
        auth_manager = keg.current_app.auth_manager
        email_enabled = flask.current_app.config.get('KEGAUTH_EMAIL_OPS_ENABLED', True)
        user_kwargs = {}
        user_kwargs['mail_enabled'] = email_enabled
        for field in form.data:
            # Only want fields that are on the class in kwargs
            # if we pass other stuff like permission_ids
            # user model wont be saved
            if hasattr(self.orm_cls, field):
                user_kwargs[field] = form[field].data
        obj = auth_manager.create_user(user_kwargs, _commit=False)
        return obj

    def update_obj(self, obj, form):
        if obj is None:
            obj = self.create_user(form)
        else:
            form.populate_obj(obj)
        # only reset a password if it is on the form and populated
        if hasattr(form, 'reset_password') and form.reset_password.data:
            obj.password = form.reset_password.data

        obj.permissions = form.get_selected_permissions()
        obj.bundles = form.get_selected_bundles()
        obj.groups = form.get_selected_groups()
        return obj

    def delete(self, objid):
        # ensure user cannot delete oneself
        if objid == flask_login.current_user.id:
            return self.on_delete_failure()
        return super(User, self).delete(objid)


@requires_permissions('auth-manage')
class Group(CrudView):
    url = '/groups'
    object_name = 'Group'
    form_cls = staticmethod(forms.group_form)

    def create_form(self, obj):
        form_cls = self.form_cls(endpoint=self.endpoint_for_action('edit'))
        return form_cls(obj=obj)

    @property
    def orm_cls(self):
        return flask.current_app.auth_manager.entity_registry.group_cls

    @property
    def grid_cls(self):
        return grids.make_group_grid(
            edit_endpoint=self.endpoint_for_action('edit'),
            edit_permission=self.permissions['edit'],
            delete_endpoint=self.endpoint_for_action('delete'),
            delete_permission=self.permissions['delete']
        )

    def update_obj(self, obj, form):
        obj = obj or self.add_orm_obj()
        form.populate_obj(obj)
        obj.permissions = form.get_selected_permissions()
        obj.bundles = form.get_selected_bundles()
        return obj


@requires_permissions('auth-manage')
class Bundle(CrudView):
    url = '/bundles'
    object_name = 'Bundle'
    form_cls = staticmethod(forms.bundle_form)

    def create_form(self, obj):
        form_cls = self.form_cls(endpoint=self.endpoint_for_action('edit'))
        return form_cls(obj=obj)

    @property
    def orm_cls(self):
        return flask.current_app.auth_manager.entity_registry.bundle_cls

    @property
    def grid_cls(self):
        return grids.make_group_grid(
            edit_endpoint=self.endpoint_for_action('edit'),
            edit_permission=self.permissions['edit'],
            delete_endpoint=self.endpoint_for_action('delete'),
            delete_permission=self.permissions['delete']
        )

    def update_obj(self, obj, form):
        obj = obj or self.add_orm_obj()
        form.populate_obj(obj)
        obj.permissions = form.get_selected_permissions()
        return obj


@requires_permissions('auth-manage')
class Permission(keg.web.BaseView):
    url = '/permissions'
    grid_template = 'keg_auth/crud-list.html'

    @property
    def grid_cls(self):
        return grids.make_permission_grid()

    def get(self):
        grid = self.grid_cls()
        grid.apply_qs_args()

        if grid.export_to:
            return grid.export_as_response()

        return flask.render_template(
            self.grid_template,
            page_title='Permissions',
            grid=grid
        )


def make_blueprint(import_name, _auth_manager, bp_name='auth', login_cls=Login,
                   forgot_cls=ForgotPassword, reset_cls=ResetPassword, logout_cls=Logout,
                   verify_cls=VerifyAccount, user_crud_cls=User, group_crud_cls=Group,
                   bundle_crud_cls=Bundle, permission_cls=Permission):
    """ Blueprint factory for keg-auth views

        Naming the blueprint here requires us to create separate view classes so that the routes
        get applied to the blueprint. Override view classes may be provided.

        Most params are assumed to be view classes. `_auth_manager` is the extension instance meant
        for the app on which this blueprint will be used: it is necessary in order to apply url
        routes for user functions.
    """
    _blueprint = flask.Blueprint(bp_name, import_name)

    # It's not ideal we have to redefine the classes, but it's needed because of how
    # Keg.web.BaseView does it's meta programming.  If we don't redefine the class, then
    # the view doesn't actually get created on blueprint.
    class Login(login_cls):
        blueprint = _blueprint
        auth_manager = _auth_manager

    class ForgotPassword(forgot_cls):
        blueprint = _blueprint
        auth_manager = _auth_manager

    class ResetPassword(reset_cls):
        blueprint = _blueprint
        auth_manager = _auth_manager

    class VerifyAccount(verify_cls):
        blueprint = _blueprint
        auth_manager = _auth_manager

    class Logout(logout_cls):
        blueprint = _blueprint

    class User(user_crud_cls):
        blueprint = _blueprint

    class Group(group_crud_cls):
        blueprint = _blueprint

    class Bundle(bundle_crud_cls):
        blueprint = _blueprint

    class Permission(permission_cls):
        blueprint = _blueprint

    return _blueprint
