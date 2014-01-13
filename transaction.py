# -*- coding: utf-8 -*-
'''

    Payment Gateway Transaction

    :copyright: (c) 2013 by Openlabs Technologies & Consulting (P) Ltd.
    :license: BSD, see LICENSE for more details

'''
from authorize import AuthorizeClient, CreditCard, Address, \
    AuthorizeResponseError
from authorize.client import AuthorizeCreditCard
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval
from trytond.model import fields

__all__ = [
    'PaymentGatewayAuthorize', 'AddPaymentProfileView', 'AddPaymentProfile',
    'AuthorizeNetTransaction',
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
        return AuthorizeClient(
            self.authorize_net_login,
            self.authorize_net_transaction_key,
            test=self.test
        )


class AuthorizeNetTransaction:
    """
    Implement the authorize and capture methods
    """
    __name__ = 'payment_gateway.transaction'

    def authorize_authorize_net(self, card_info=None):
        """
        Authorize using authorize.net for the specific transaction.

        :param credit_card: An instance of CreditCardView
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        client = self.gateway.get_authorize_client()
        client._transaction.base_params['x_currency_code'] = self.currency.code

        if card_info:
            cc = CreditCard(
                card_info.number,
                card_info.expiry_year,
                card_info.expiry_month,
                card_info.csc,
                card_info.owner,
            )
            credit_card = client.card(cc)
        elif self.payment_profile:
            credit_card = client.saved_card(
                self.payment_profile.provider_reference
            )
        else:
            self.raise_user_error('no_card_or_profile')

        try:
            result = credit_card.auth(self.amount)
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'authorized'
            self.provider_reference = str(result.uid)
            self.save()
            TransactionLog.serialize_and_create(self, result.full_response)

    def settle_authorize_net(self):
        """
        Settles this transaction if it is a previous authorization.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        client = self.gateway.get_authorize_client()
        client._transaction.base_params['x_currency_code'] = self.currency.code

        auth_net_transaction = client.transaction(self.provider_reference)
        try:
            result = auth_net_transaction.settle()
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'completed'
            self.provider_reference = str(result.uid)
            self.save()
            TransactionLog.serialize_and_create(self, result.full_response)
            self.safe_post()

    def capture_authorize_net(self, card_info=None):
        """
        Capture using authorize.net for the specific transaction.

        :param card_info: An instance of CreditCardView
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        client = self.gateway.get_authorize_client()
        client._transaction.base_params['x_currency_code'] = self.currency.code

        if card_info:
            cc = CreditCard(
                card_info.number,
                card_info.expiry_year,
                card_info.expiry_month,
                card_info.csc,
                card_info.owner,
            )
            credit_card = client.card(cc)
        elif self.payment_profile:
            credit_card = client.saved_card(
                self.payment_profile.provider_reference
            )
        else:
            self.raise_user_error('no_card_or_profile')

        try:
            result = credit_card.capture(self.amount)
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'completed'
            self.provider_reference = str(result.uid)
            self.save()
            TransactionLog.serialize_and_create(self, result.full_response)
            self.safe_post()

    def retry_authorize_net(self, credit_card=None):
        """
        Authorize using Authorize.net for the specific transaction.

        :param credit_card: An instance of CreditCardView
        """
        raise self.raise_user_error('feature_not_available')

    def update_authorize_net(self):
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

        client = self.gateway.get_authorize_client()
        client._transaction.base_params['x_currency_code'] = self.currency.code

        auth_net_transaction = client.transaction(self.provider_reference)

        # Try to void the transaction
        result = auth_net_transaction.void()

        # Mark the state as cancelled
        self.state = 'cancel'
        self.save()

        TransactionLog.serialize_and_create(self, result.full_response)


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

        client = card_info.gateway.get_authorize_client()
        cc = CreditCard(
            card_info.number,
            card_info.expiry_year,
            card_info.expiry_month,
            card_info.csc,
            card_info.owner,
        )
        address = Address(
            card_info.address.street,
            card_info.address.city,
            card_info.address.zip,
            card_info.address.country.code,
        )
        saved_card = AuthorizeCreditCard(
            client,
            credit_card=cc,
            address=address,
            email=card_info.party.email
        )
        saved_card = saved_card.save()
        return self.create_profile(saved_card.uid)
