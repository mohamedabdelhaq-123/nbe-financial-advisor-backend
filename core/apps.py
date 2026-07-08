from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"

    def ready(self):
        # drf-spectacular's OpenApiAuthenticationExtension subclasses (and
        # any future schema extensions) register themselves via a metaclass
        # the moment their module is imported — unlike Django admin's
        # admin.py, there's no automatic discovery of a schema.py file, so
        # this import is what actually makes core/schema.py's extensions
        # take effect.
        import core.schema  # noqa: F401
