from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = 'Create (or reset) the EduTrack administrator account'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin', help='Admin username (default: admin)')
        parser.add_argument('--password', default='Admin@1234', help='Admin password (default: Admin@1234)')
        parser.add_argument('--email',    default='admin@edutrack.local', help='Admin email')

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        email    = options['email']

        user, created = User.objects.get_or_create(username=username, defaults={'email': email})
        user.set_password(password)
        user.is_staff     = True
        user.is_superuser = True
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'Admin account created — username: {username}'))
        else:
            self.stdout.write(self.style.WARNING(f'Existing account updated  — username: {username}'))

        self.stdout.write(self.style.SUCCESS(f'Password : {password}'))
        self.stdout.write('Run "python manage.py create_admin --username X --password Y" to change credentials.')
