"""
Data Export Functions
=====================
Export markets and price history to CSV and JSON formats.
"""
import csv
import json
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from pathlib import Path
from structlog import get_logger
from sqlalchemy import select, func
from base_engine.data.database import Market, MarketPrice, Trade, DataQualityIssue

logger = get_logger()


class DataExporter:
    """Export data to various formats"""
    
    def __init__(self, database):
        self.db = database
    
    async def export_markets_csv(self, output_path: str = "markets_export.csv") -> int:
        """
        Export markets to CSV.
        
        Args:
            output_path: Path to output CSV file
        
        Returns:
            Number of markets exported
        """
        if self.db.session_factory is None:
            logger.error("Database not available for export")
            return 0
        
        async with self.db.get_session() as session:
            result = await session.execute(
                select(Market).order_by(Market.liquidity.desc())
            )
            markets = result.scalars().all()
            
            if not markets:
                logger.warning("No markets to export")
                return 0
            
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'id', 'condition_id', 'question', 'category', 'slug',
                    'yes_token_id', 'no_token_id', 'yes_price', 'no_price',
                    'liquidity', 'volume', 'active', 'resolved', 'updated_at'
                ])
                
                for market in markets:
                    writer.writerow([
                        market.id,
                        market.condition_id or '',
                        market.question or '',
                        market.category or '',
                        market.slug or '',
                        market.yes_token_id or '',
                        market.no_token_id or '',
                        market.yes_price or '',
                        market.no_price or '',
                        market.liquidity or 0.0,
                        market.volume or 0.0,
                        1 if market.active else 0,
                        1 if getattr(market, 'resolved', False) else 0,
                        market.updated_at.isoformat() if market.updated_at else ''
                    ])
            
            logger.info(f"Exported {len(markets)} markets to {output_path}")
            return len(markets)
    
    async def export_price_history_csv(
        self,
        output_path: str = "price_history_export.csv",
        market_id: Optional[str] = None
    ) -> int:
        """
        Export price history to CSV, optionally filtered by market.
        
        Args:
            output_path: Path to output CSV file
            market_id: Optional market ID to filter by
        
        Returns:
            Number of price records exported
        """
        if self.db.session_factory is None:
            logger.error("Database not available for export")
            return 0
        
        async with self.db.get_session() as session:
            query = select(
                MarketPrice.market_id,
                Market.question,
                MarketPrice.token_id,
                MarketPrice.side,
                MarketPrice.price,
                MarketPrice.timestamp
            ).join(Market, MarketPrice.market_id == Market.id)
            
            if market_id:
                query = query.where(MarketPrice.market_id == market_id)
            
            query = query.order_by(MarketPrice.market_id, MarketPrice.timestamp)
            
            result = await session.execute(query)
            prices = result.all()
            
            if not prices:
                logger.warning("No price history to export")
                return 0
            
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'market_id', 'question', 'token_id', 'side',
                    'price', 'timestamp_unix', 'timestamp_readable'
                ])
                
                for price in prices:
                    timestamp_unix = int(price.timestamp.timestamp()) if price.timestamp else 0
                    timestamp_readable = price.timestamp.isoformat() if price.timestamp else ''
                    
                    writer.writerow([
                        price.market_id,
                        price.question[:100] if price.question else '',  # Truncate long questions
                        price.token_id or '',
                        price.side or '',
                        price.price or 0.0,
                        timestamp_unix,
                        timestamp_readable
                    ])
            
            logger.info(f"Exported {len(prices)} price records to {output_path}")
            return len(prices)

    async def export_trades_csv(
        self,
        output_path: str = "trades_export.csv",
        market_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100_000,
    ) -> int:
        """
        Export trades to CSV (#40). Optionally filter by market_id and since.
        Returns number of rows exported.
        """
        if self.db.session_factory is None:
            logger.error("Database not available for export")
            return 0
        async with self.db.get_session() as session:
            query = select(Trade).order_by(Trade.timestamp.desc()).limit(limit)
            if market_id:
                query = query.where(Trade.market_id == market_id)
            if since:
                query = query.where(Trade.timestamp >= since)
            result = await session.execute(query)
            trades = result.scalars().all()
        if not trades:
            logger.warning("No trades to export")
            return 0
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "market_id", "token_id", "user_address", "bot_id", "side",
                "size", "price", "pnl", "entry_time", "exit_time", "timestamp"
            ])
            for t in trades:
                writer.writerow([
                    t.id or "",
                    t.market_id or "",
                    t.token_id or "",
                    t.user_address or "",
                    t.bot_id or "",
                    t.side or "",
                    t.size or 0,
                    t.price or 0,
                    t.pnl or "",
                    t.entry_time.isoformat() if t.entry_time else "",
                    t.exit_time.isoformat() if t.exit_time else "",
                    t.timestamp.isoformat() if t.timestamp else "",
                ])
        logger.info("Exported %s trades to %s", len(trades), output_path)
        return len(trades)
    
    async def export_to_json(
        self,
        output_path: str = "polymarket_export.json",
        include_price_history: bool = True
    ) -> Dict[str, Any]:
        """
        Export complete dataset to JSON with nested price history.
        
        Args:
            output_path: Path to output JSON file
            include_price_history: Whether to include price history in export
        
        Returns:
            Dictionary with export summary
        """
        if self.db.session_factory is None:
            logger.error("Database not available for export")
            return {"error": "Database not available"}
        
        async with self.db.get_session() as session:
            # Get markets
            result = await session.execute(
                select(Market).order_by(Market.liquidity.desc())
            )
            markets = result.scalars().all()
            
            markets_data = []
            total_price_records = 0
            
            for market in markets:
                market_dict = {
                    "id": market.id,
                    "condition_id": market.condition_id,
                    "question": market.question,
                    "category": market.category,
                    "slug": market.slug,
                    "yes_token_id": market.yes_token_id,
                    "no_token_id": market.no_token_id,
                    "yes_price": float(market.yes_price) if market.yes_price else None,
                    "no_price": float(market.no_price) if market.no_price else None,
                    "liquidity": float(market.liquidity) if market.liquidity else 0.0,
                    "volume": float(market.volume) if market.volume else 0.0,
                    "active": market.active,
                    "resolved": getattr(market, "resolved", False),
                    "updated_at": market.updated_at.isoformat() if market.updated_at else None
                }
                
                if include_price_history:
                    # Get price history for this market
                    price_result = await session.execute(
                        select(MarketPrice)
                        .where(MarketPrice.market_id == market.id)
                        .order_by(MarketPrice.timestamp)
                    )
                    price_history = price_result.scalars().all()
                    
                    market_dict["price_history"] = [
                        {
                            "token_id": p.token_id,
                            "side": p.side,
                            "price": float(p.price) if p.price else 0.0,
                            "timestamp": p.timestamp.isoformat() if p.timestamp else None
                        }
                        for p in price_history
                    ]
                    total_price_records += len(price_history)
                else:
                    market_dict["price_history"] = []
                
                markets_data.append(market_dict)
            
            # Get quality issues
            result = await session.execute(
                select(DataQualityIssue).order_by(DataQualityIssue.detected_at.desc())
            )
            quality_issues = result.scalars().all()
            
            export_data = {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "markets_count": len(markets_data),
                "total_price_records": total_price_records,
                "quality_issues_count": len(quality_issues),
                "markets": markets_data,
                "quality_issues": [
                    {
                        "market_id": issue.market_id,
                        "issue_type": issue.issue_type,
                        "description": issue.description,
                        "detected_at": issue.detected_at.isoformat() if issue.detected_at else None
                    }
                    for issue in quality_issues
                ]
            }
            
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, default=str)
            
            logger.info(f"Exported complete dataset to {output_path}")
            logger.info(f"  - {len(markets_data)} markets")
            logger.info(f"  - {total_price_records} price records")
            logger.info(f"  - {len(quality_issues)} quality issues")
            
            return {
                "markets": len(markets_data),
                "price_records": total_price_records,
                "quality_issues": len(quality_issues),
                "output_path": output_path
            }
