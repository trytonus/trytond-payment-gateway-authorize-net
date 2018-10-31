# -*- coding: utf-8 -*-
"""
    party

    :license: see LICENSE for details.
"""
import authorize
from authorize.exceptions import AuthorizeInvalidError, \
    AuthorizeResponseError

from trytond.model import fields
from trytond.rpc import RPC
from trytond.pool import PoolMeta, Pool

__metaclass__ = PoolMeta
__all__ = ['Party', 'Address', 'PaymentProfile']


class Party:
    __name__ = 'party.party'

    def _get_authorize_net_customer_id(self, gateway_id):
        """
        Extracts and returns customer id from party's payment profile
        Return None if no customer id is found.

        :param gateway_id: The gateway ID to which the customer id is associated
        """
        PaymentProfile = Pool().get('party.payment_profile')

        payment_profiles = PaymentProfile.search([
            ('party', '=', self.id),
            ('authorize_profile_id', '!=', None),
            ('gateway', '=', gateway_id),
        ])
        if payment_profiles:
            return payment_profiles[0].authorize_profile_id
        return None

    def create_auth_profile(self):
        """
        Creates a customer profile on authorize.net and returns
        created profile's ID
        """
        try:
            customer = authorize.Customer.create({
                'description': self.name,
                'email': self.email,
            })
        except AuthorizeInvalidError, exc:
            self.raise_user_error(unicode(exc))

        return customer.customer_id


class Address:
    __name__ = 'party.address'

    authorize_id = fields.Char(
        'Authorize.net ID', readonly=True
    )

    def send_to_authorize(self, profile_id):
        """
        Helpler method which creates a new address record on
        authorize.net servers and returns it's ID.

        :param profile_id: The profile_id of customer profile for
            which you want to create address. Required if create=True
        """
        Address = Pool().get('party.address')

        for try_count in range(2):
            try:
                address = authorize.Address.create(
                    profile_id, self.get_authorize_address()
                )
                break
            except AuthorizeResponseError, exc:
                if try_count == 0 and (
                        'E00039' in unicode(exc) or
                        'E00043' in unicode(exc)
                ):
                    # Delete all addresses on authorize.net
                    self.delete_authorize_addresses(profile_id)
                    continue
                self.raise_user_error(unicode(exc))
            except AuthorizeInvalidError, exc:
                self.raise_user_error(unicode(exc))

        address_id = address.address_id

        Address.write([self], {
            'authorize_id': address_id,
        })
        return address_id

    def get_authorize_address(self, name=None):
        """
        Returns address as a dictionary to send to authorize.net

        :param name: Name to send as first name in address.
            Default is party's name.
        """
        name = name or self.name or self.party.name

        try:
            first_name, last_name = name.split(" ", 1)
        except ValueError:
            first_name = name
            last_name = ""

        return {
            'first_name': first_name[:50],
            'last_name': last_name[:50],
            'company': self.party.name[:50],
            'address': '\n'.join(filter(None, [self.street, self.streetbis])),
            'city': self.city,
            'state': self.subdivision and self.subdivision.code,
            'zip': self.zip,
            'country': self.country and self.country.code,
            'phone_number': self.party.phone,
            'fax_number': self.party.fax,
        }

    def delete_authorize_addresses(self, profile_id):
        """
        Delete all shipping addresses for customer on authorize.net
        """
        Address = Pool().get('party.address')

        customer_details = authorize.Customer.details(profile_id)
        address_ids = [
            a.address_id for a in customer_details.profile.addresses
        ]
        for address_id in address_ids:
            authorize.Address.delete(profile_id, address_id)

        # Set authorize_id none for all party addresses
        Address.write(list(self.party.addresses), {
            'authorize_id': None,
        })


class PaymentProfile:
    __name__ = 'party.payment_profile'

    authorize_profile_id = fields.Char(
        'Authorize.net Profile ID', readonly=True
    )

    @classmethod
    def __setup__(cls):
        super(PaymentProfile, cls).__setup__()
        cls.__rpc__.update({
            'create_profile_using_authorize_net_nonce': RPC(
                instantiate=0, readonly=False
            )
        })

    @classmethod
    def create_profile_using_authorize_net_nonce(
        cls, user_id, gateway_id, nonce_data, address_id=None
    ):
        """
        Create a Payment Profile using nonce_data returned by auth.net using
        accept.js
        """
        Address = Pool().get('party.address')
        Party = Pool().get('party.party')
        PaymentGateway = Pool().get('payment_gateway.gateway')
        PaymentProfile = Pool().get('party.payment_profile')

        opaque_data = nonce_data['opaqueData']
        customer_info = nonce_data['customerInformation']
        card_info = nonce_data['encryptedCardData']

        party = Party(user_id)
        gateway = PaymentGateway(gateway_id)
        assert gateway.provider == 'authorize_net'
        gateway.get_authorize_client()

        customer_id = party._get_authorize_net_customer_id(
            gateway.id
        )
        if not customer_id:
            customer_id = party.create_auth_profile()

        card_data = {
            'opaque_data': {
                'data_descriptor': opaque_data['dataDescriptor'],
                'data_value': opaque_data['dataValue'],
            }
        }
        if address_id:
            address_data = Address(address_id).get_authorize_address()
            card_data['billing'] = address_data

        try:
            credit_card = authorize.CreditCard.create(
                customer_id, card_data
            )
        except AuthorizeInvalidError, exc:
            cls.raise_user_error(unicode(exc))
        except AuthorizeResponseError, exc:
            if 'E00039' in unicode(exc):
                # Delete all unused payment profiles on authorize.net
                customer_details = authorize.Customer.details(customer_id)
                auth_payment_ids = set([
                    p.payment_id for p in customer_details.profile.payments
                ])
                if party.payment_profiles:
                    local_payment_ids = set([
                        p.provider_reference for p in party.payment_profiles  # noqa
                    ])
                    ids_to_delete = auth_payment_ids.difference(
                        local_payment_ids
                    )
                else:
                    ids_to_delete = auth_payment_ids

                if ids_to_delete:
                    for payment_id in ids_to_delete:
                        authorize.CreditCard.delete(customer_id, payment_id)
            cls.raise_user_error(unicode(exc))

        name = (
            customer_info.get('firstName', '') +
            customer_info.get('lastName', '')
        )
        expiry_month, expiry_year = card_info['expDate'].split('/')

        profile, = PaymentProfile.create([{
            'name': name or party.name,
            'party': party.id,
            'address': address_id or party.addresses[0].id,
            'gateway': gateway.id,
            'last_4_digits': card_info['cardNumber'][-4:],
            'expiry_month': expiry_month,
            'expiry_year': expiry_year,
            'provider_reference': credit_card.payment_id,
            'authorize_profile_id': customer_id,
        }])
        return profile.id
