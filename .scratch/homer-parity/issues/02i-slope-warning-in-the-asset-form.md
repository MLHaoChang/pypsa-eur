# 02i — Show the fuel curve slope warning in the generator form

Status: done

Follow-up from homer-parity 02d, asked for by UX (21).

## Problem

A fuel curve slope above 0 and below 1 MWh of fuel per MWh warns only in the pre-run checklist (`fuel_curve_slope_low`). The generator form accepts the value without a hint, so the slip is seen only at run time.

## Wanted

An inline warning hint under the Fuel curve slope field, worded exactly as `fuel_curve.slope_warning`, never blocking the save.

## Tests

- Shots in Expert and Guided, reviewed by UX.
