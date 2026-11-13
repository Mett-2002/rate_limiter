"""Rename the caller model without replacing records, credentials or grants."""
from django.db import migrations, models


def rename_permissions(apps, schema_editor, old_model, new_model, label):
    alias = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    # migrate normally injects Django's content-type rename. This also supports
    # MigrationExecutor and preserves IDs used by permissions and admin logs.
    ContentType.objects.using(alias).filter(
        app_label="limiter", model=old_model,
    ).update(model=new_model)
    for action in ("add", "change", "delete", "view"):
        Permission.objects.using(alias).filter(
            content_type__app_label="limiter",
            content_type__model=new_model,
            codename=f"{action}_{old_model}",
        ).update(codename=f"{action}_{new_model}", name=f"Can {action} {label}")


def forwards(apps, schema_editor):
    rename_permissions(apps, schema_editor, "service", "apiclient", "API client")


def backwards(apps, schema_editor):
    rename_permissions(apps, schema_editor, "apiclient", "service", "service")


class Migration(migrations.Migration):
    dependencies = [
        ("limiter", "0003_service_policy_version_and_more"),
        ("auth", "0012_alter_user_first_name_max_length"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RenameModel(old_name="Service", new_name="APIClient"),
        migrations.AlterModelOptions(
            name="apiclient",
            options={"verbose_name": "API client", "verbose_name_plural": "API clients"},
        ),
        migrations.RemoveConstraint(model_name="apiclient", name="service_limit_positive"),
        migrations.RemoveConstraint(model_name="apiclient", name="service_window_positive"),
        migrations.AddConstraint(
            model_name="apiclient",
            constraint=models.CheckConstraint(condition=models.Q(max_requests__gte=1), name="api_client_limit_positive"),
        ),
        migrations.AddConstraint(
            model_name="apiclient",
            constraint=models.CheckConstraint(condition=models.Q(window_seconds__gte=1), name="api_client_window_positive"),
        ),
        migrations.RunPython(forwards, backwards),
    ]
