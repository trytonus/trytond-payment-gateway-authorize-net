# -*- coding: utf-8 -*-
'''

    Payment Gateway

    :copyright: (c) 2013 by Openlabs Technologies & Consulting (P) Ltd.
    :license: BSD, see LICENSE for more details

'''
from trytond.pool import Pool
from .transaction import PaymentGatewayAuthorize, \
    AddPaymentProfileView, AddPaymentProfile, AuthorizeNetTransaction


def register():
    Pool.register(
        PaymentGatewayAuthorize,
        AddPaymentProfileView,
        AuthorizeNetTransaction,
        module='payment_gateway_authorize_net', type_='model'
    )
    Pool.register(
        AddPaymentProfile,
        module='payment_gateway_authorize_net', type_='wizard'
    )
