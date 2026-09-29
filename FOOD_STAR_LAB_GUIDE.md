# Food Star-Up / Lab Update

This update adds star-up for Food directly inside `/lab`.

## Food star-up rule

Food does not use Memory Stones.

- **5×** food of the same element/type and current star tier -> **1×** food of the next star tier.
- `Rainbow` food is treated as its own type, so Rainbow food only combines with Rainbow food.
- Food can be starred from **4★ through 20★**.
- The process is atomic in the database, so a failed operation does not partially consume food.

Examples:

- 5× Blaze 4★ -> 1× Blaze 5★
- 5× Blaze 5★ -> 1× Blaze 6★
- 5× Rainbow 10★ -> 1× Rainbow 11★

## Lab buttons

`/lab` now has three actions:

1. **Melt Down a Warrior** - existing feature.
2. **Star Up Food** - choose one food stack and perform one star-up.
3. **Mass Star Up Food** - confirm once, then automatically perform every possible food star-up, including chained conversions through multiple tiers.

For example, 25× Blaze 4★ will be processed as:

25× 4★ -> 5× 5★ -> 1× 6★

## Files changed

- `bot.py` - Lab UI, single Food star-up selector, mass Food star-up confirmation.
- `database.py` - Food star-up and mass Food star-up database operations.
- `progression.py` - Food star-up constants.

No card artwork or character art was changed in this update.
