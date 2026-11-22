-- Make gig_deposits available only to authenticated PRS admins.
-- This restores the booking/edit/contract workflow while keeping deposits private.
CREATE POLICY gig_deposits_admin_all
ON public.gig_deposits
FOR ALL
TO authenticated
USING (public.is_admin(auth.uid()))
WITH CHECK (public.is_admin(auth.uid()));

-- Pin search paths for functions used by RLS and privileged server-side operations.
ALTER FUNCTION public.is_admin(uuid)
SET search_path = pg_catalog, public;

ALTER FUNCTION public.delete_gig_full(uuid)
SET search_path = pg_catalog, public;

-- The app's admin delete page performs the deletion through its server-side service client.
-- Do not expose the SECURITY DEFINER helper to browser/session roles.
REVOKE EXECUTE ON FUNCTION public.delete_gig_full(uuid)
FROM PUBLIC, anon, authenticated;
