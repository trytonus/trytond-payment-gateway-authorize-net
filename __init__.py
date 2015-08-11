# -*- coding: utf-8 -*-
from trytond.pool import Pool
from .transaction import PaymentGatewayAuthorize, \
    AddPaymentProfileView, AddPaymentProfile, AuthorizeNetTransaction, \
    Party, Address, PaymentProfile


def register():
    Pool.register(
        PaymentGatewayAuthorize,
        AddPaymentProfileView,
        AuthorizeNetTransaction,
        PaymentProfile,
        Party,
        Address,
        module='payment_gateway_authorize_net', type_='model'
    )
    Pool.register(
        AddPaymentProfile,
        module='payment_gateway_authorize_net', type_='wizard'
    )
