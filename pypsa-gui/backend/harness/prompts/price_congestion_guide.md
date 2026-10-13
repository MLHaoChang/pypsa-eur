---
constant: _PRICE_CONGESTION_GUIDE
trailing_space: [facts]
---

Price-driver / congestion narration (#4). `full` = facts + chaining.

## facts

Price + congestion narration. LMP = locational marginal price at a bus =
dual of that bus's nodal power balance. The marginal unit is the generator
whose marginal cost sets the price at that bus and hour. A line dual /
congestion rent is the shadow price of a line's flow limit — nonzero means
the line is binding (congested); the congestion spread is the price
difference across that congested line.

## chaining

To explain why prices are high, CHAIN get_results prices + get_results
price_drivers + get_results line_duals and narrate the marginal unit and any
binding lines.
