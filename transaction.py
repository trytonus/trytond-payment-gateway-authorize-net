# -*- coding: utf-8 -*-
'''

    Payment Gateway Transaction

    :copyright: (c) 2013 by Openlabs Technologies & Consulting (P) Ltd.
    :license: BSD, see LICENSE for more details

'''
import authorize
from authorize.exceptions import AuthorizeInvalidError, \
    AuthorizeResponseError
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval
from trytond.model import fields

__all__ = [
    'PaymentGatewayAuthorize', 'AddPaymentProfileView', 'AddPaymentProfile',
    'AuthorizeNetTransaction', 'Party', 'Address'
]
__metaclass__ = PoolMeta


class PaymentGatewayAuthorize:
    "Authorize.net Gateway Implementation"
    __name__ = 'payment_gateway.gateway'

    authorize_net_login = fields.Char(
        'API Login', states={
            'required': Eval('provider') == 'authorize_net',
            'invisible': Eval('provider') != 'authorize_net',
        }, depends=['provider']
    )
    authorize_net_transaction_key = fields.Char(
        'Transaction Key', states={
            'required': Eval('provider') == 'authorize_net',
            'invisible': Eval('provider') != 'authorize_net',
        }, depends=['provider']
    )

    @classmethod
    def get_providers(cls, values=None):
        """
        Downstream modules can add to the list
        """
        rv = super(PaymentGatewayAuthorize, cls).get_providers()
        authorize_record = ('authorize_net', 'Authorize.net')
        if authorize_record not in rv:
            rv.append(authorize_record)
        return rv

    def get_methods(self):
        if self.provider == 'authorize_net':
            return [
                ('credit_card', 'Credit Card - Authorize.net'),
            ]
        return super(PaymentGatewayAuthorize, self).get_methods()

    def get_authorize_client(self):
        """
        Return an authenticated authorize.net client.
        """
        assert self.provider == 'authorize_net', 'Invalid provider'
        authorize.Configuration.configure(
            authorize.Environment.TEST if self.test else authorize.Environment.PRODUCTION,  # noqa
            self.authorize_net_login,
            self.authorize_net_transaction_key,
        )


class AuthorizeNetTransaction:
    """
    Implement the authorize and capture methods
    """
    __name__ = 'payment_gateway.transaction'

    @classmethod
    def __setup__(cls):
        super(AuthorizeNetTransaction, cls).__setup__()

        cls._error_messages.update({
            'cancel_only_authorized': 'Only authorized transactions can be' + (
                ' cancelled.'),
        })

    def authorize_authorize_net(self, card_info=None):
        """
        Authorize using authorize.net for the specific transaction.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        # Initialize authorize client
        self.gateway.get_authorize_client()

        auth_data = {
            'amount': self.amount,
        }
        if hasattr(self, 'sale') and self.sale:
            auth_data.update({
                'order': {
                    'invoice_number': self.sale.reference or '',
                    'description': self.sale.description or '',
                },
            })
        if card_info:
            billing_address = self.address.get_authorize_address(
                card_info.owner)
            shipping_address = {}
            if self.shipping_address:
                shipping_address = self.shipping_address.get_authorize_address(
                    card_info.owner)

            auth_data.update({
                'email': self.party.email,
                'credit_card': {
                    'card_number': card_info.number,
                    'card_code': str(card_info.csc),
                    'expiration_date': "%s/%s" % (
                        card_info.expiry_month, card_info.expiry_year
                    ),
                },
                'billing': billing_address,
                'shipping': shipping_address,
            })

        elif self.payment_profile:
            if self.shipping_address:
                if self.shipping_address.authorize_id:
                    address_id = self.shipping_address.authorize_id
                else:
                    address_id = self.shipping_address.send_to_authorize(
                        self.party.authorize_profile_id)
            else:
                if self.address.authorize_id:
                    address_id = self.address.authorize_id
                else:
                    address_id = self.address.send_to_authorize(
                        self.party.authorize_profile_id)
            auth_data.update({
                'customer_id': self.party.authorize_profile_id,
                'payment_id': self.payment_profile.provider_reference,
                'address_id': address_id,
            })
        else:
            self.raise_user_error('no_card_or_profile')

        try:
            result = authorize.Transaction.auth(auth_data)
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'authorized'
            self.provider_reference = str(result.transaction_response.trans_id)
            self.save()
            TransactionLog.serialize_and_create(self, result)

    def settle_authorize_net(self):
        """
        Settles this transaction if it is a previous authorization.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        # Initialize authorize.net client
        self.gateway.get_authorize_client()

        try:
            result = authorize.Transaction.settle(
                self.provider_reference, self.amount
            )
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'completed'
            self.provider_reference = str(result.transaction_response.trans_id)
            self.save()
            TransactionLog.serialize_and_create(self, result)
            self.safe_post()

    def capture_authorize_net(self, card_info=None):
        """
        Capture using authorize.net for the specific transaction.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        # Initialize authorize client
        self.gateway.get_authorize_client()

        capture_data = {
            'amount': self.amount,
        }
        if hasattr(self, 'sale') and self.sale:
            capture_data.update({
                'order': {
                    'invoice_number': self.sale.reference or '',
                    'description': self.sale.description or '',
                },
            })
        if card_info:
            billing_address = self.address.get_authorize_address(
                card_info.owner)
            shipping_address = {}
            if self.shipping_address:
                shipping_address = self.shipping_address.get_authorize_address(
                    card_info.owner)

            capture_data.update({
                'email': self.party.email,
                'credit_card': {
                    'card_number': card_info.number,
                    'card_code': str(card_info.csc),
                    'expiration_date': "%s/%s" % (
                        card_info.expiry_month, card_info.expiry_year
                    ),
                },
                'billing': billing_address,
                'shipping': shipping_address,
            })

        elif self.payment_profile:
            if self.shipping_address:
                if self.shipping_address.authorize_id:
                    address_id = self.shipping_address.authorize_id
                else:
                    address_id = self.shipping_address.send_to_authorize(
                        self.party.authorize_profile_id)
            else:
                if self.address.authorize_id:
                    address_id = self.address.authorize_id
                else:
                    address_id = self.address.send_to_authorize(
                        self.party.authorize_profile_id)
            capture_data.update({
                'customer_id': self.party.authorize_profile_id,
                'payment_id': self.payment_profile.provider_reference,
                'address_id': address_id,
            })
        else:
            self.raise_user_error('no_card_or_profile')

        try:
            result = authorize.Transaction.sale(capture_data)
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'completed'
            self.provider_reference = str(result.transaction_response.trans_id)
            self.save()
            TransactionLog.serialize_and_create(self, result)
            self.safe_post()

    def retry_authorize_net(self, credit_card=None):  # pragma: no cover
        """
        Authorize using Authorize.net for the specific transaction.

        :param credit_card: An instance of CreditCardView
        """
        raise self.raise_user_error('feature_not_available')

    def update_authorize_net(self):  # pragma: no cover
        """
        Update the status of the transaction from Authorize.net
        """
        raise self.raise_user_error('feature_not_available')

    def cancel_authorize_net(self):
        """
        Cancel this authorization or request
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        if self.state != 'authorized':
            self.raise_user_error('cancel_only_authorized')

        # Initialize authurize.net client
        self.gateway.get_authorize_client()

        # Try to void the transaction
        try:
            result = authorize.Transaction.void(self.provider_reference)
        except AuthorizeResponseError, exc:
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'cancel'
            self.save()
            TransactionLog.serialize_and_create(self, result)


class AddPaymentProfileView:
    __name__ = 'party.payment_profile.add_view'

    @classmethod
    def get_providers(cls):
        """
        Return the list of providers who support credit card profiles.
        """
        res = super(AddPaymentProfileView, cls).get_providers()
        res.append(('authorize_net', 'Authorize.net'))
        return res


class AddPaymentProfile:
    """
    Add a payment profile
    """
    __name__ = 'party.party.payment_profile.add'

    def transition_add_authorize_net(self):
        """
        Handle the case if the profile should be added for authorize.net
        """
        card_info = self.card_info

        # Initialize authorize.net client
        card_info.gateway.get_authorize_client()

        # Create new customer profile if no old profile is there
        if not card_info.party.authorize_profile_id:
            customer_id = self.create_auth_customer_profile()
        else:
            customer_id = card_info.party.authorize_profile_id

        # Now create new credit card and associate it with the above
        # created customer
        credit_card_data = {
            'card_number': card_info.number,
            'card_code': str(card_info.csc),
            'expiration_month': card_info.expiry_month,
            'expiration_year': card_info.expiry_year,
            'billing': card_info.address.get_authorize_address(card_info.owner)
        }
        for try_count in range(2):
            try:
                credit_card = authorize.CreditCard.create(
                    customer_id, credit_card_data
                )
                break
            except AuthorizeInvalidError, exc:
                self.raise_user_error(unicode(exc))
            except AuthorizeResponseError, exc:
                if try_count == 0 and 'E00039' in unicode(exc):
                    # Delete all unused payment profiles on authorize.net
                    customer_details = authorize.Customer.details(customer_id)
                    auth_payment_ids = set([
                        p.payment_id for p in customer_details.profile.payments
                    ])
                    if card_info.party.payment_profiles:
                        local_payment_ids = set([
                            p.provider_reference for p in card_info.party.payment_profiles  # noqa
                        ])
                        ids_to_delete = auth_payment_ids.difference(
                            local_payment_ids
                        )
                    else:
                        ids_to_delete = auth_payment_ids

                    if ids_to_delete:
                        for payment_id in ids_to_delete:
                            authorize.CreditCard.delete(customer_id, payment_id)
                    continue
                self.raise_user_error(unicode(exc.message))

        return self.create_profile(credit_card.payment_id)

    def create_auth_customer_profile(self):
        """
        Creates a customer profile on authorize.net and returns
        created profile's ID
        """
        Party = Pool().get('party.party')

        customer_party = self.card_info.party
        try:
            customer = authorize.Customer.create({
                'description': customer_party.name,
                'email': customer_party.email,
            })
        except AuthorizeInvalidError, exc:
            self.raise_user_error(unicode(exc))

        # Write customer profile ID in party
        Party.write([customer_party], {
            'authorize_profile_id': customer.customer_id
        })
        return customer.customer_id


class Party:
    __name__ = 'party.party'

    authorize_profile_id = fields.Char(
        'Authorize.net Profile ID', readonly=True
    )


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
                    customer_details = authorize.Customer.details(profile_id)
                    address_ids = [
                        a.address_id for a in customer_details.profile.addresses
                    ]
                    for address_id in address_ids:
                        authorize.Address.delete(profile_id, address_id)
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
        return {
            'first_name': name or self.name or self.party.name,
            'last_name': '',
            'company': '',
            'address': '\n'.join(filter(None, [self.street, self.streetbis])),
            'city': self.city,
            'state': self.subdivision and self.subdivision.code,
            'zip': self.zip,
            'country': self.country and self.country.code,
            'phone_number': self.party.phone,
            'fax_number': self.party.fax,
        }
