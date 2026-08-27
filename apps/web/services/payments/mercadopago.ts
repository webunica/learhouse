'use server';

import { getAPIUrl } from '@services/config/config';
import { RequestBodyWithAuthHeader, errorHandling, secureFetch } from '@services/utils/ts/requests';

export interface MercadoPagoPreferenceResponse {
  id: string;
  init_point: string;
  sandbox_init_point?: string;
}

export interface MercadoPagoConfigResponse {
  is_enabled: boolean;
  public_key: string;
  default_currency: string;
}

/**
 * Get MercadoPago configuration from the backend (checks if active and gets public key)
 */
export async function getMercadoPagoConfig(): Promise<MercadoPagoConfigResponse> {
  try {
    const res = await fetch(`${getAPIUrl()}payments/mercadopago/config`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) {
      return { is_enabled: false, public_key: '', default_currency: 'CLP' };
    }
    return await res.json();
  } catch (err) {
    console.error('Error fetching MercadoPago config:', err);
    return { is_enabled: false, public_key: '', default_currency: 'CLP' };
  }
}

/**
 * Creates a MercadoPago Checkout preference for purchasing a course
 */
export async function createMercadoPagoPreference(
  courseUuid: string,
  unitPrice: number = 10000,
  currencyId: string = 'CLP',
  title?: string,
  accessToken?: string
): Promise<MercadoPagoPreferenceResponse | null> {
  try {
    const payload = {
      course_uuid: courseUuid,
      unit_price: unitPrice,
      currency_id: currencyId,
      title: title || undefined,
    };

    const result = await secureFetch(
      `${getAPIUrl()}payments/mercadopago/preference`,
      RequestBodyWithAuthHeader('POST', payload, null, accessToken || '')
    );

    const res = await errorHandling(result);
    return res;
  } catch (error) {
    console.error('Failed to create MercadoPago preference:', error);
    throw error;
  }
}
