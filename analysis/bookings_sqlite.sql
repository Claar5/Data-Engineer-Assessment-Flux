-- =============================================================================
-- Flux bookings -- SQLite translation (runs against data/warehouse/flux.db)
-- Status treatment: revenue = CONFIRMED only. Cancelled and pending bookings are kept in the views (flags + separate revenue columns) but excluded from revenue in sections 4 and 5.
-- =============================================================================
DROP VIEW IF EXISTS v_booking_revenue;
DROP VIEW IF EXISTS v_bookings_current_zar;
DROP VIEW IF EXISTS v_bookings_current;


-- 1. Current version of each booking ------------------------------------------

CREATE VIEW v_bookings_current AS
WITH ranked AS (
    SELECT
        f.*,
        ROW_NUMBER() OVER (
            PARTITION BY booking_id
            ORDER BY updated_at DESC, booking_sk DESC
        ) AS version_rank
    FROM fact_bookings AS f
)
SELECT *
FROM ranked
WHERE version_rank = 1;


-- 2. Point-in-time revenue in ZAR ---------------------------------------------
CREATE VIEW v_bookings_current_zar AS
WITH fx AS (
    SELECT
        currency_code,
        rate_to_zar,
        date(valid_from) AS valid_from,
        LEAD(date(valid_from)) OVER (PARTITION BY currency_code ORDER BY valid_from) AS valid_to
    FROM dim_fx_rate
)
SELECT
    b.booking_id,
    b.property_sk,
    b.check_in_date,
    b.check_out_date,
    b.num_guests,
    b.room_rate,
    b.currency_code,
    b.revenue_amount,
    fx.rate_to_zar AS fx_rate_to_zar,
    ROUND(b.revenue_amount * fx.rate_to_zar, 2) AS revenue_zar,
    b.booking_channel,
    b.booking_status,
    b.is_cancelled,
    b.created_at,
    b.updated_at,
    b.source_batch,
    b.loaded_at
FROM v_bookings_current AS b
LEFT JOIN fx
      ON fx.currency_code = b.currency_code
      AND date(b.created_at) >= fx.valid_from
      AND (fx.valid_to IS NULL OR date(b.created_at) < fx.valid_to);

SELECT booking_id, currency_code, created_at, revenue_amount, fx_rate_to_zar, revenue_zar, booking_status
FROM v_bookings_current_zar
ORDER BY booking_id
LIMIT 10;


-- 3. Reporting view -----------------------------------------------------------
CREATE VIEW v_booking_revenue AS
SELECT
    b.booking_id,
    p.property_id,
    p.property_name,
    p.country,
    p.region,
    b.booking_channel,
    b.booking_status,
    b.is_cancelled,
    date(b.check_in_date) AS check_in_date,
    date(b.check_out_date) AS check_out_date,
    strftime('%Y-%m-01', b.check_in_date) AS stay_month,
    CAST(julianday(b.check_out_date) - julianday(b.check_in_date) AS INTEGER) AS nights,
    b.num_guests,
    b.currency_code,
    b.revenue_amount,
    b.fx_rate_to_zar,
    b.revenue_zar,
    CASE WHEN b.booking_status = 'confirmed' THEN b.revenue_zar ELSE 0 END AS recognised_revenue_zar,
    CASE WHEN b.booking_status = 'pending'   THEN b.revenue_zar ELSE 0 END AS pending_revenue_zar,
    CASE WHEN b.booking_status = 'cancelled' THEN b.revenue_zar ELSE 0 END AS cancelled_revenue_zar,
    b.created_at,
    b.updated_at
FROM v_bookings_current_zar AS b
LEFT JOIN dim_property AS cur_sk ON cur_sk.property_sk = b.property_sk
LEFT JOIN dim_property AS p
      ON p.property_id = cur_sk.property_id
      AND p.is_current = 1;


-- 4. Month-on-month revenue growth, confirmed bookings, 2025 (ZAR) ------------
WITH RECURSIVE months(month) AS (
    SELECT '2025-01-01'
    UNION ALL
    SELECT date(month, '+1 month') FROM months WHERE month < '2025-12-01'
),
monthly AS (
    SELECT stay_month AS month, SUM(revenue_zar) AS revenue_zar
    FROM v_booking_revenue
    WHERE booking_status = 'confirmed'
      AND check_in_date BETWEEN '2025-01-01' AND '2025-12-31'
    GROUP BY stay_month
),
series AS (
    SELECT
        m.month,
        COALESCE(r.revenue_zar, 0) AS revenue_zar,
        LAG(COALESCE(r.revenue_zar, 0)) OVER (ORDER BY m.month) AS prev_month_revenue_zar
    FROM months AS m
    LEFT JOIN monthly AS r ON r.month = m.month
)
SELECT
    month,
    ROUND(revenue_zar, 2) AS revenue_zar,
    ROUND(prev_month_revenue_zar, 2) AS prev_month_revenue_zar,
    ROUND(100.0 * (revenue_zar - prev_month_revenue_zar) / NULLIF(prev_month_revenue_zar, 0), 2) AS mom_growth_pct
FROM series
ORDER BY month;


-- 5. Top 2 properties by revenue within each country (ZAR, confirmed) ---------
WITH property_revenue AS (
    SELECT country, property_id, property_name, SUM(revenue_zar) AS revenue_zar
    FROM v_booking_revenue
    WHERE booking_status = 'confirmed'
    GROUP BY country, property_id, property_name
),
ranked AS (
    SELECT *, RANK() OVER (PARTITION BY country ORDER BY revenue_zar DESC) AS revenue_rank
    FROM property_revenue
)
SELECT country, revenue_rank, property_id, property_name, ROUND(revenue_zar, 2) AS revenue_zar
FROM ranked
WHERE revenue_rank <= 2
ORDER BY country, revenue_rank, property_id;

