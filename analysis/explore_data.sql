--DROP TABLE dim_fx_rate ;
--DROP TABLE dim_property;
--DROP TABLE fact_bookings ;
--DROP TABLE merge_audit;
--DROP TABLE rejects ;


SELECT * FROM dim_fx_rate;
SELECT * FROM dim_property;
SELECT * FROM fact_bookings;
select count(*), booking_id FROM fact_bookings GROUP BY booking_id HAVING COUNT(*) > 1;
SELECT * FROM merge_audit;
SELECT * FROM rejects;

SELECT * FROM  fact_bookings where booking_id = 'B1009'