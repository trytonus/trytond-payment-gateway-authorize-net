# -*- coding: utf-8 -*-
"""
    party

    :license: see LICENSE for details.
"""
import authorize
from authorize.exceptions import AuthorizeInvalidError, \
    AuthorizeResponseError

from trytond.model import fields
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
                if try_count == 0 and 'E00039' in unicode(exc):
                    # Delete all addresses on authorize.net
                    self.delete_authorize_addresses(profile_id)
                    continue
                self.raise_user_error(unicode(exc.message))
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
