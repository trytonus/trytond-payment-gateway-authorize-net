# -*- coding: utf-8 -*-
import authorize
from authorize.exceptions import AuthorizeInvalidError, \
    AuthorizeResponseError
from trytond.pool import PoolMeta, Pool
from trytond.pyson import Eval
from trytond.model import fields

__all__ = [
    'PaymentGatewayAuthorize', 'AddPaymentProfile', 'AuthorizeNetTransaction'
]
__metaclass__ = PoolMeta


class PaymentGatewayAuthorize:
    "Authorize.net Gateway Implementation"
    __name__ = 'payment_gateway.gateway'

    authorize_net_login = fields.Char(
        'API Login', states={
            'required': Eval('provider') == 'authorize_net',
            'invisible': Eval('provider') != 'authorize_net',
            'readonly': ~Eval('active', True),
        }, depends=['provider', 'active']
    )
    authorize_net_transaction_key = fields.Char(
        'Transaction Key', states={
            'required': Eval('provider') == 'authorize_net',
            'invisible': Eval('provider') != 'authorize_net',
            'readonly': ~Eval('active', True),
        }, depends=['provider', 'active']
    )
    authorize_net_client_key = fields.Char(
        'Client Key', states={
            'required': Eval('provider') == 'authorize_net',
            'invisible': Eval('provider') != 'authorize_net',
            'readonly': ~Eval('active', True),
        }, depends=['provider', 'active']
    )

    @classmethod
    def view_attributes(cls):
        return super(PaymentGatewayAuthorize, cls).view_attributes() + [
            ('//notebook/page[@id="authorize_net"]', 'states', {
                'invisible': Eval('provider') != 'authorize_net'
            })]

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

        auth_data = self.get_authorize_net_request_data()
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
                        self.payment_profile.authorize_profile_id)
            else:
                if self.address.authorize_id:
                    address_id = self.address.authorize_id
                else:
                    address_id = self.address.send_to_authorize(
                        self.payment_profile.authorize_profile_id)
            auth_data.update({
                'customer_id': self.payment_profile.authorize_profile_id,
                'payment_id': self.payment_profile.provider_reference,
                'shipping_id': address_id,
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
            # Following response codes are given:
            # 1 -- Approved
            # 2 -- Declined
            # 3 -- Error
            # 4 -- Held for Review
            self.provider_reference = str(result.transaction_response.trans_id)
            self.last_four_digits = card_info.number[-4:] if card_info else \
                self.payment_profile.last_4_digits
            if result.transaction_response.response_code == '1':
                self.state = 'authorized'
            elif result.transaction_response.response_code == '4':
                self.state = 'in-progress'
            else:
                self.state = 'failed'
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
            # Following response codes are given:
            # 1 -- Approved
            # 2 -- Declined
            # 3 -- Error
            # 4 -- Held for Review
            self.provider_reference = str(result.transaction_response.trans_id)
            if result.transaction_response.response_code == '1':
                self.state = 'completed'
            elif result.transaction_response.response_code == '4':
                self.state = 'in-progress'
            else:
                self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, result)
            if self.state == 'completed':
                self.safe_post()

    def capture_authorize_net(self, card_info=None):
        """
        Capture using authorize.net for the specific transaction.
        """
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        # Initialize authorize client
        self.gateway.get_authorize_client()

        capture_data = self.get_authorize_net_request_data()
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
                        self.payment_profile.authorize_profile_id)
            else:
                if self.address.authorize_id:
                    address_id = self.address.authorize_id
                else:
                    address_id = self.address.send_to_authorize(
                        self.payment_profile.authorize_profile_id)
            capture_data.update({
                'customer_id': self.payment_profile.authorize_profile_id,
                'payment_id': self.payment_profile.provider_reference,
                'shipping_id': address_id,
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
            # Following response codes are given:
            # 1 -- Approved
            # 2 -- Declined
            # 3 -- Error
            # 4 -- Held for Review
            self.provider_reference = str(result.transaction_response.trans_id)
            self.last_four_digits = card_info.number[-4:] if card_info else \
                self.payment_profile.last_4_digits
            if result.transaction_response.response_code == '1':
                self.state = 'completed'
            elif result.transaction_response.response_code == '4':
                self.state = 'in-progress'
            else:
                self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, result)
            if self.state == 'completed':
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
        TransactionLog = Pool().get('payment_gateway.transaction.log')
        result = authorize.Transaction.details(self.provider_reference)
        if result.transaction.response_code == '1':
            if result.transaction.transaction_type in (
                    'authCaptureTransaction', 'priorAuthCaptureTransaction'
            ):
                self.state = 'completed'
            elif result.transaction.transaction_type == 'authorizeOnlyTransaction':  # noqa
                self.state = 'authorized'
        elif result.transaction.response_code == '4':
            pass
        else:
            self.state = 'failed'
        self.save()
        TransactionLog.serialize_and_create(self, result)
        if self.state == 'completed':
            self.safe_post()

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

    def get_authorize_net_request_data(self):
        """
        Downstream modules can modify this method to send extra data to
        authorize.net

        Ref: http://vcatalano.github.io/py-authorize/transaction.html
        """
        return {
            'amount': self.amount
        }

    def refund_authorize_net(self):
        TransactionLog = Pool().get('payment_gateway.transaction.log')

        # Initialize authorize.net client
        self.gateway.get_authorize_client()

        try:
            result = authorize.Transaction.refund({
                'amount': self.amount,
                'last_four': self.last_four_digits,
                'transaction_id': self.origin.provider_reference,
            })
        except AuthorizeResponseError, exc:
            self.state = 'failed'
            self.save()
            TransactionLog.serialize_and_create(self, exc.full_response)
        else:
            self.state = 'completed'
            self.save()
            TransactionLog.serialize_and_create(self, result)
            self.safe_post()


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

        customer_id = card_info.party._get_authorize_net_customer_id(
            card_info.gateway.id
        )
        # Create new customer profile if no old profile is there
        if not customer_id:
            customer_id = self.card_info.party.create_auth_profile()

        # Now create new credit card and associate it with the above
        # created customer
        credit_card_data = {
            'credit_card': {
                'card_number': card_info.number,
                'card_code': str(card_info.csc),
                'expiration_date': "%s/%s" % (
                    card_info.expiry_month, card_info.expiry_year
                ),
            },
            'billing': card_info.address.get_authorize_address(card_info.owner)
        }
        for try_count in range(2):
            try:
                credit_card = authorize.CreditCard.create(
                    customer_id, credit_card_data
                )
                # Validate newly created credit card
                authorize.CreditCard.validate(
                    customer_id, credit_card.payment_id, {
                        'card_code':
                            credit_card_data['credit_card']['card_code'],
                        'validationMode': 'testMode' if card_info.gateway.test
                        else 'liveMode'
                    }
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

        return self.create_profile(
            credit_card.payment_id,
            authorize_profile_id=customer_id
        )
