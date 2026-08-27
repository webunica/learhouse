'use client';

import React, { useState } from 'react';
import { Button } from '@components/ui/button';
import { Loader2, CreditCard } from 'lucide-react';
import { createMercadoPagoPreference } from '@services/payments/mercadopago';
import { useUser } from '@components/Contexts/UserContext';
import { useRouter } from 'next/navigation';

interface MercadoPagoButtonProps {
  courseUuid: string;
  price?: number;
  currency?: string;
  title?: string;
  buttonText?: string;
  className?: string;
  onSuccess?: () => void;
}

export function MercadoPagoButton({
  courseUuid,
  price = 10000,
  currency = 'CLP',
  title,
  buttonText = 'Pagar con MercadoPago',
  className = '',
}: MercadoPagoButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const user = useUser() as any;
  const router = useRouter();

  const handlePayment = async () => {
    if (!user || !user.id) {
      router.push('/login');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const pref = await createMercadoPagoPreference(
        courseUuid,
        price,
        currency,
        title,
        user.access_token || undefined
      );

      if (pref && pref.init_point) {
        // Redirect to MercadoPago Checkout Pro
        window.location.href = pref.init_point;
      } else {
        setError('No se pudo generar el link de pago.');
      }
    } catch (err: any) {
      console.error('Error starting MercadoPago checkout:', err);
      setError(err?.message || 'Error al conectar con MercadoPago.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex flex-col gap-2 w-full">
      <Button
        onClick={handlePayment}
        disabled={loading}
        className={`w-full py-3 px-4 font-semibold text-white bg-[#009EE3] hover:bg-[#0081bb] transition-colors rounded-xl flex items-center justify-center gap-2 shadow-sm ${className}`}
      >
        {loading ? (
          <>
            <Loader2 className="w-5 h-5 animate-spin" />
            <span>Generando pago...</span>
          </>
        ) : (
          <>
            <CreditCard className="w-5 h-5" />
            <span>{buttonText}</span>
          </>
        )}
      </Button>

      {error && (
        <p className="text-xs text-red-500 text-center font-medium mt-1">
          {error}
        </p>
      )}
    </div>
  );
}

export default MercadoPagoButton;
