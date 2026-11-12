from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework import serializers
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, OpenApiParameter
from core import api, identity

router = DefaultRouter()
for resource, model in api.RESOURCES.items():
    view = type(model.__name__ + 'ViewSet', (api.ReadResource,), {'resource': resource, 'serializer_class': api.SERIALIZERS[resource], '__module__': 'core.api'})
    router.register(resource, view, basename=resource)

urlpatterns = [path('api/v1/', include(router.urls)), path('api/v1/session/', identity.session_view),
               path('api/v1/mfa/', identity.mfa_view), path('api/v1/dashboard/', api.Dashboard.as_view()),
               path('api/v1/trace/<int:pk>/', api.Trace.as_view()), path('health/', api.health),
               path('api/schema/', SpectacularAPIView.as_view()), path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'))]
urlpatterns[-2] = path('api/schema/', SpectacularAPIView.as_view(), name='schema')
for command, (_, scope, fields) in api.COMMANDS.items():
    attrs = {k.rstrip('?'): api.field(t, not k.endswith('?')) for k, t in fields.items()}
    if command == 'imports/preview':
        attrs['csv'] = serializers.CharField(max_length=1024 * 1024, trim_whitespace=False)
    request_serializer = type(command.replace('/', '_').replace('-', '_') + 'Request', (serializers.Serializer,), attrs)
    view = type(command.replace('/', '_') + 'View', (api.CommandView,), {'command': command, 'scope': scope, 'request_serializer': request_serializer})
    view = extend_schema(request=request_serializer, responses=OpenApiTypes.OBJECT, parameters=[OpenApiParameter('Idempotency-Key', str, location=OpenApiParameter.HEADER, required=True)])(view)
    urlpatterns.append(path('api/v1/commands/' + command + '/', view.as_view()))
for resource, (model, fields, _) in api.catalog.CATALOG.items():
    meta = type('Meta', (), {'model': model, 'fields': fields})
    request_serializer = type('Create' + model.__name__, (serializers.ModelSerializer,), {'Meta': meta})
    view = type('Create' + model.__name__ + 'View', (api.CatalogCreate,), {'resource': resource, 'request_serializer': request_serializer})
    view = extend_schema(request=request_serializer, responses=OpenApiTypes.OBJECT, parameters=[OpenApiParameter('Idempotency-Key', str, location=OpenApiParameter.HEADER, required=True)])(view)
    urlpatterns.append(path('api/v1/commands/catalog/' + resource + '/', view.as_view()))
    update_meta = type('Meta', (), {'model': model, 'fields': fields, 'extra_kwargs': {f: {'required': False} for f in fields}})
    update_serializer = type('Update' + model.__name__, (serializers.ModelSerializer,), {'Meta': update_meta})
    update_view = type('Update' + model.__name__ + 'View', (api.CatalogUpdate,), {'resource': resource, 'request_serializer': update_serializer})
    update_view = extend_schema(request=update_serializer, responses=OpenApiTypes.OBJECT, parameters=[OpenApiParameter('Idempotency-Key', str, location=OpenApiParameter.HEADER, required=True)])(update_view)
    urlpatterns.append(path('api/v1/commands/catalog/' + resource + '/<int:pk>/update/', update_view.as_view()))
