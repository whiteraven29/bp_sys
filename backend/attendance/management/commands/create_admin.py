from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = 'Create (or reset, with --force) the EduTrack administrator account'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin', help='Admin username (default: admin)')
        parser.add_argument('--password', default=None, help='Admin password (required — no default)')
        parser.add_argument('--email', default='admin@edutrack.local', help='Admin email')
        parser.add_argument(
            '--force', action='store_true',
            help='Allow resetting the password of an account that already exists.',
        )

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']
        email = options['email']
        force = options['force']

        if not password:
            raise CommandError(
                'Refusing to create an admin without an explicit --password '
                '(there is no default — choose a strong, unique one).'
            )
        if len(password) < 8:
            raise CommandError('Password must be at least 8 characters.')

        user = User.objects.filter(username=username).first()
        if user is not None and not force:
            raise CommandError(
                f'An account named "{username}" already exists. '
                'Pass --force to intentionally reset its password.'
            )

        created = user is None
        if created:
            user = User(username=username, email=email)
        user.set_password(password)
        user.is_staff = True
        user.is_superuser = True
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f'Admin account created — username: {username}'))
        else:
            self.stdout.write(self.style.WARNING(f'Existing account password reset — username: {username}'))
