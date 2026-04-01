"""
Station Registry — maps Polymarket weather market cities to NOAA weather stations.

Each station has exact coordinates matching the resolution source used by Polymarket.
Resolution typically uses the city's primary airport ASOS/METAR station via
Weather Underground or NOAA CDO (Climate Data Online).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone as _tz
from typing import Dict, List, Optional

import aiohttp
from structlog import get_logger

logger = get_logger()


@dataclass(frozen=True)
class WeatherStation:
    city_name: str
    station_id: str          # ICAO code: KLGA, EGLC, etc.
    ghcnd_id: str            # GHCND:USW00014732
    latitude: float
    longitude: float
    elevation_m: float
    timezone: str            # IANA timezone
    temp_unit: str           # "F" or "C"
    aliases: tuple = ()      # lowercase aliases for matching market text
    resolution_source: str = ""
    has_asos_1min: bool = False  # True for US ASOS stations (K-prefix ICAO)
    local_model: Optional[str] = None  # Open-Meteo local hi-res model slug (e.g. "meteofrance_seamless")


# ── Registry ─────────────────────────────────────────────────────────────
# Coordinates target the exact ASOS station, NOT the city centroid.
# Verified against Polymarket resolution rules and Weather Underground IDs.

STATION_REGISTRY: Dict[str, WeatherStation] = {

    # ── Original US cities ──────────────────────────────────────────────
    "new_york_city": WeatherStation(
        city_name="New York City",
        station_id="KLGA",
        ghcnd_id="GHCND:USW00014732",
        latitude=40.7772,
        longitude=-73.8726,
        elevation_m=6.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("nyc", "new york city", "new york"),
        resolution_source="Weather Underground / KLGA",
        has_asos_1min=True,
    ),
    "atlanta": WeatherStation(
        city_name="Atlanta",
        station_id="KATL",
        ghcnd_id="GHCND:USW00013874",
        latitude=33.6407,
        longitude=-84.4277,
        elevation_m=315.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("atlanta",),
        resolution_source="Weather Underground / KATL",
        has_asos_1min=True,
    ),
    "seattle": WeatherStation(
        city_name="Seattle",
        station_id="KSEA",
        ghcnd_id="GHCND:USW00024233",
        latitude=47.4502,
        longitude=-122.3088,
        elevation_m=131.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("seattle",),
        resolution_source="Weather Underground / KSEA",
        has_asos_1min=True,
    ),
    "dallas": WeatherStation(
        city_name="Dallas",
        station_id="KDFW",
        ghcnd_id="GHCND:USW00003927",
        latitude=32.8998,
        longitude=-97.0403,
        elevation_m=171.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("dallas",),
        resolution_source="Weather Underground / KDFW",
        has_asos_1min=True,
    ),
    "miami": WeatherStation(
        city_name="Miami",
        station_id="KMIA",
        ghcnd_id="GHCND:USW00012839",
        latitude=25.7959,
        longitude=-80.2870,
        elevation_m=2.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("miami",),
        resolution_source="Weather Underground / KMIA",
        has_asos_1min=True,
    ),
    "chicago": WeatherStation(
        city_name="Chicago",
        station_id="KORD",
        ghcnd_id="GHCND:USW00094846",
        latitude=41.9742,
        longitude=-87.9073,
        elevation_m=201.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("chicago",),
        resolution_source="Weather Underground / KORD",
        has_asos_1min=True,
    ),
    "denver": WeatherStation(
        city_name="Denver",
        station_id="KDEN",
        ghcnd_id="GHCND:USW00003017",
        latitude=39.8561,
        longitude=-104.6737,
        elevation_m=1655.0,
        timezone="America/Denver",
        temp_unit="F",
        aliases=("denver",),
        resolution_source="Weather Underground / KDEN",
        has_asos_1min=True,
    ),

    # ── Expanded US cities ──────────────────────────────────────────────
    "los_angeles": WeatherStation(
        city_name="Los Angeles",
        station_id="KLAX",
        ghcnd_id="GHCND:USW00023174",
        latitude=33.9425,
        longitude=-118.4081,
        elevation_m=38.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("los angeles", "la", "lax"),
        resolution_source="Weather Underground / KLAX",
        has_asos_1min=True,
    ),
    "phoenix": WeatherStation(
        city_name="Phoenix",
        station_id="KPHX",
        ghcnd_id="GHCND:USW00026412",
        latitude=33.4373,
        longitude=-112.0078,
        elevation_m=337.0,
        timezone="America/Phoenix",
        temp_unit="F",
        aliases=("phoenix",),
        resolution_source="Weather Underground / KPHX",
        has_asos_1min=True,
    ),
    "houston": WeatherStation(
        city_name="Houston",
        station_id="KIAH",
        ghcnd_id="GHCND:USW00012960",
        latitude=29.9844,
        longitude=-95.3414,
        elevation_m=30.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("houston",),
        resolution_source="Weather Underground / KIAH",
        has_asos_1min=True,
    ),
    "philadelphia": WeatherStation(
        city_name="Philadelphia",
        station_id="KPHL",
        ghcnd_id="GHCND:USW00013739",
        latitude=39.8721,
        longitude=-75.2411,
        elevation_m=11.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("philadelphia", "philly"),
        resolution_source="Weather Underground / KPHL",
        has_asos_1min=True,
    ),
    "san_francisco": WeatherStation(
        city_name="San Francisco",
        station_id="KSFO",
        ghcnd_id="GHCND:USW00023234",
        latitude=37.6197,
        longitude=-122.3647,
        elevation_m=3.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("san francisco", "sf"),
        resolution_source="Weather Underground / KSFO",
        has_asos_1min=True,
    ),
    "boston": WeatherStation(
        city_name="Boston",
        station_id="KBOS",
        ghcnd_id="GHCND:USW00014739",
        latitude=42.3606,
        longitude=-71.0097,
        elevation_m=4.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("boston",),
        resolution_source="Weather Underground / KBOS",
        has_asos_1min=True,
    ),
    "washington_dc": WeatherStation(
        city_name="Washington D.C.",
        station_id="KDCA",
        ghcnd_id="GHCND:USW00013743",
        latitude=38.8521,
        longitude=-77.0377,
        elevation_m=4.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("washington d.c.", "washington dc", "washington, d.c.", "washington, dc", "dc"),
        resolution_source="Weather Underground / KDCA",
        has_asos_1min=True,
    ),
    "minneapolis": WeatherStation(
        city_name="Minneapolis",
        station_id="KMSP",
        ghcnd_id="GHCND:USW00014922",
        latitude=44.8848,
        longitude=-93.2223,
        elevation_m=287.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("minneapolis", "minneapolis-saint paul", "twin cities"),
        resolution_source="Weather Underground / KMSP",
        has_asos_1min=True,
    ),
    "detroit": WeatherStation(
        city_name="Detroit",
        station_id="KDTW",
        ghcnd_id="GHCND:USW00094847",
        latitude=42.2124,
        longitude=-83.3534,
        elevation_m=195.0,
        timezone="America/Detroit",
        temp_unit="F",
        aliases=("detroit",),
        resolution_source="Weather Underground / KDTW",
        has_asos_1min=True,
    ),
    "las_vegas": WeatherStation(
        city_name="Las Vegas",
        station_id="KLAS",
        ghcnd_id="GHCND:USW00023169",
        latitude=36.0801,
        longitude=-115.1522,
        elevation_m=664.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("las vegas",),
        resolution_source="Weather Underground / KLAS",
        has_asos_1min=True,
    ),
    "portland": WeatherStation(
        city_name="Portland",
        station_id="KPDX",
        ghcnd_id="GHCND:USW00024229",
        latitude=45.5898,
        longitude=-122.5951,
        elevation_m=10.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("portland", "portland or", "portland oregon"),
        resolution_source="Weather Underground / KPDX",
        has_asos_1min=True,
    ),
    "nashville": WeatherStation(
        city_name="Nashville",
        station_id="KBNA",
        ghcnd_id="GHCND:USW00013897",
        latitude=36.1245,
        longitude=-86.6782,
        elevation_m=183.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("nashville",),
        resolution_source="Weather Underground / KBNA",
        has_asos_1min=True,
    ),
    "salt_lake_city": WeatherStation(
        city_name="Salt Lake City",
        station_id="KSLC",
        ghcnd_id="GHCND:USW00024127",
        latitude=40.7884,
        longitude=-111.9778,
        elevation_m=1288.0,
        timezone="America/Denver",
        temp_unit="F",
        aliases=("salt lake city", "slc"),
        resolution_source="Weather Underground / KSLC",
        has_asos_1min=True,
    ),
    "kansas_city": WeatherStation(
        city_name="Kansas City",
        station_id="KMCI",
        ghcnd_id="GHCND:USW00003971",
        latitude=39.2976,
        longitude=-94.7138,
        elevation_m=315.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("kansas city",),
        resolution_source="Weather Underground / KMCI",
        has_asos_1min=True,
    ),
    "orlando": WeatherStation(
        city_name="Orlando",
        station_id="KMCO",
        ghcnd_id="GHCND:USW00012815",
        latitude=28.4294,
        longitude=-81.3089,
        elevation_m=30.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("orlando",),
        resolution_source="Weather Underground / KMCO",
        has_asos_1min=True,
    ),
    "tampa": WeatherStation(
        city_name="Tampa",
        station_id="KTPA",
        ghcnd_id="GHCND:USW00012842",
        latitude=27.9755,
        longitude=-82.5332,
        elevation_m=9.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("tampa",),
        resolution_source="Weather Underground / KTPA",
        has_asos_1min=True,
    ),
    "charlotte": WeatherStation(
        city_name="Charlotte",
        station_id="KCLT",
        ghcnd_id="GHCND:USW00013881",
        latitude=35.2140,
        longitude=-80.9431,
        elevation_m=228.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("charlotte",),
        resolution_source="Weather Underground / KCLT",
        has_asos_1min=True,
    ),
    "new_orleans": WeatherStation(
        city_name="New Orleans",
        station_id="KMSY",
        ghcnd_id="GHCND:USW00012916",
        latitude=29.9934,
        longitude=-90.2580,
        elevation_m=1.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("new orleans",),
        resolution_source="Weather Underground / KMSY",
        has_asos_1min=True,
    ),
    "indianapolis": WeatherStation(
        city_name="Indianapolis",
        station_id="KIND",
        ghcnd_id="GHCND:USW00093819",
        latitude=39.7173,
        longitude=-86.2944,
        elevation_m=245.0,
        timezone="America/Indiana/Indianapolis",
        temp_unit="F",
        aliases=("indianapolis", "indy"),
        resolution_source="Weather Underground / KIND",
        has_asos_1min=True,
    ),
    "columbus": WeatherStation(
        city_name="Columbus",
        station_id="KCMH",
        ghcnd_id="GHCND:USW00014821",
        latitude=39.9980,
        longitude=-82.8919,
        elevation_m=247.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("columbus", "columbus oh", "columbus ohio"),
        resolution_source="Weather Underground / KCMH",
        has_asos_1min=True,
    ),
    "memphis": WeatherStation(
        city_name="Memphis",
        station_id="KMEM",
        ghcnd_id="GHCND:USW00013958",
        latitude=35.0425,
        longitude=-89.9767,
        elevation_m=87.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("memphis",),
        resolution_source="Weather Underground / KMEM",
        has_asos_1min=True,
    ),
    "louisville": WeatherStation(
        city_name="Louisville",
        station_id="KSDF",
        ghcnd_id="GHCND:USW00093821",
        latitude=38.1774,
        longitude=-85.7361,
        elevation_m=149.0,
        timezone="America/Kentucky/Louisville",
        temp_unit="F",
        aliases=("louisville",),
        resolution_source="Weather Underground / KSDF",
        has_asos_1min=True,
    ),
    "austin": WeatherStation(
        city_name="Austin",
        station_id="KAUS",
        ghcnd_id="GHCND:USW00013904",
        latitude=30.1945,
        longitude=-97.6699,
        elevation_m=168.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("austin",),
        resolution_source="Weather Underground / KAUS",
        has_asos_1min=True,
    ),
    "san_antonio": WeatherStation(
        city_name="San Antonio",
        station_id="KSAT",
        ghcnd_id="GHCND:USW00012921",
        latitude=29.5337,
        longitude=-98.4698,
        elevation_m=240.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("san antonio",),
        resolution_source="Weather Underground / KSAT",
        has_asos_1min=True,
    ),
    "san_diego": WeatherStation(
        city_name="San Diego",
        station_id="KSAN",
        ghcnd_id="GHCND:USW00023188",
        latitude=32.7338,
        longitude=-117.1933,
        elevation_m=9.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("san diego",),
        resolution_source="Weather Underground / KSAN",
        has_asos_1min=True,
    ),
    "sacramento": WeatherStation(
        city_name="Sacramento",
        station_id="KSMF",
        ghcnd_id="GHCND:USW00023232",
        latitude=38.6954,
        longitude=-121.5908,
        elevation_m=8.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("sacramento",),
        resolution_source="Weather Underground / KSMF",
        has_asos_1min=True,
    ),
    "pittsburgh": WeatherStation(
        city_name="Pittsburgh",
        station_id="KPIT",
        ghcnd_id="GHCND:USW00094823",
        latitude=40.4915,
        longitude=-80.2329,
        elevation_m=367.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("pittsburgh",),
        resolution_source="Weather Underground / KPIT",
        has_asos_1min=True,
    ),
    "st_louis": WeatherStation(
        city_name="St. Louis",
        station_id="KSTL",
        ghcnd_id="GHCND:USW00013994",
        latitude=38.7487,
        longitude=-90.3700,
        elevation_m=172.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("st. louis", "st louis", "saint louis"),
        resolution_source="Weather Underground / KSTL",
        has_asos_1min=True,
    ),
    "baltimore": WeatherStation(
        city_name="Baltimore",
        station_id="KBWI",
        ghcnd_id="GHCND:USW00093721",
        latitude=39.1754,
        longitude=-76.6682,
        elevation_m=46.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("baltimore",),
        resolution_source="Weather Underground / KBWI",
        has_asos_1min=True,
    ),
    "raleigh": WeatherStation(
        city_name="Raleigh",
        station_id="KRDU",
        ghcnd_id="GHCND:USW00013722",
        latitude=35.8776,
        longitude=-78.7875,
        elevation_m=132.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("raleigh", "raleigh-durham", "raleigh durham"),
        resolution_source="Weather Underground / KRDU",
        has_asos_1min=True,
    ),
    "oklahoma_city": WeatherStation(
        city_name="Oklahoma City",
        station_id="KOKC",
        ghcnd_id="GHCND:USW00013967",
        latitude=35.3931,
        longitude=-97.6008,
        elevation_m=397.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("oklahoma city", "okc"),
        resolution_source="Weather Underground / KOKC",
        has_asos_1min=True,
    ),
    "omaha": WeatherStation(
        city_name="Omaha",
        station_id="KOMA",
        ghcnd_id="GHCND:USW00094918",
        latitude=41.3032,
        longitude=-95.8942,
        elevation_m=299.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("omaha",),
        resolution_source="Weather Underground / KOMA",
        has_asos_1min=True,
    ),
    "albuquerque": WeatherStation(
        city_name="Albuquerque",
        station_id="KABQ",
        ghcnd_id="GHCND:USW00023049",
        latitude=35.0402,
        longitude=-106.6090,
        elevation_m=1633.0,
        timezone="America/Denver",
        temp_unit="F",
        aliases=("albuquerque",),
        resolution_source="Weather Underground / KABQ",
        has_asos_1min=True,
    ),
    "tucson": WeatherStation(
        city_name="Tucson",
        station_id="KTUS",
        ghcnd_id="GHCND:USW00023160",
        latitude=32.1161,
        longitude=-110.9410,
        elevation_m=779.0,
        timezone="America/Phoenix",
        temp_unit="F",
        aliases=("tucson",),
        resolution_source="Weather Underground / KTUS",
        has_asos_1min=True,
    ),
    "el_paso": WeatherStation(
        city_name="El Paso",
        station_id="KELP",
        ghcnd_id="GHCND:USW00023044",
        latitude=31.8072,
        longitude=-106.3764,
        elevation_m=1194.0,
        timezone="America/Denver",
        temp_unit="F",
        aliases=("el paso",),
        resolution_source="Weather Underground / KELP",
        has_asos_1min=True,
    ),
    "jacksonville": WeatherStation(
        city_name="Jacksonville",
        station_id="KJAX",
        ghcnd_id="GHCND:USW00012918",
        latitude=30.4941,
        longitude=-81.6878,
        elevation_m=9.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("jacksonville",),
        resolution_source="Weather Underground / KJAX",
        has_asos_1min=True,
    ),
    "richmond": WeatherStation(
        city_name="Richmond",
        station_id="KRIC",
        ghcnd_id="GHCND:USW00013707",
        latitude=37.5052,
        longitude=-77.3197,
        elevation_m=52.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("richmond",),
        resolution_source="Weather Underground / KRIC",
        has_asos_1min=True,
    ),
    "buffalo": WeatherStation(
        city_name="Buffalo",
        station_id="KBUF",
        ghcnd_id="GHCND:USW00014733",
        latitude=42.9401,
        longitude=-78.7322,
        elevation_m=215.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("buffalo",),
        resolution_source="Weather Underground / KBUF",
        has_asos_1min=True,
    ),
    "reno": WeatherStation(
        city_name="Reno",
        station_id="KRNO",
        ghcnd_id="GHCND:USW00023185",
        latitude=39.4991,
        longitude=-119.7688,
        elevation_m=1344.0,
        timezone="America/Los_Angeles",
        temp_unit="F",
        aliases=("reno",),
        resolution_source="Weather Underground / KRNO",
        has_asos_1min=True,
    ),
    "tulsa": WeatherStation(
        city_name="Tulsa",
        station_id="KTUL",
        ghcnd_id="GHCND:USW00013940",
        latitude=36.1984,
        longitude=-95.8881,
        elevation_m=206.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("tulsa",),
        resolution_source="Weather Underground / KTUL",
        has_asos_1min=True,
    ),
    "wichita": WeatherStation(
        city_name="Wichita",
        station_id="KICT",
        ghcnd_id="GHCND:USW00003928",
        latitude=37.6499,
        longitude=-97.4331,
        elevation_m=406.0,
        timezone="America/Chicago",
        temp_unit="F",
        aliases=("wichita",),
        resolution_source="Weather Underground / KICT",
        has_asos_1min=True,
    ),
    "cleveland": WeatherStation(
        city_name="Cleveland",
        station_id="KCLE",
        ghcnd_id="GHCND:USW00014820",
        latitude=41.4094,
        longitude=-81.8547,
        elevation_m=244.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("cleveland",),
        resolution_source="Weather Underground / KCLE",
        has_asos_1min=True,
    ),
    "erie": WeatherStation(
        city_name="Erie",
        station_id="KERI",
        ghcnd_id="GHCND:USW00014860",
        latitude=42.0831,
        longitude=-80.1762,
        elevation_m=223.0,
        timezone="America/New_York",
        temp_unit="F",
        aliases=("erie",),
        resolution_source="Weather Underground / KERI",
        has_asos_1min=True,
    ),

    "honolulu": WeatherStation(
        city_name="Honolulu",
        station_id="PHNL",
        ghcnd_id="GHCND:USW00022521",
        latitude=21.3187,
        longitude=-157.9224,
        elevation_m=4.0,
        timezone="Pacific/Honolulu",
        temp_unit="F",
        aliases=("honolulu", "hawaii"),
        resolution_source="Weather Underground / PHNL",
    ),
    "anchorage": WeatherStation(
        city_name="Anchorage",
        station_id="PANC",
        ghcnd_id="GHCND:USW00026451",
        latitude=61.1744,
        longitude=-149.9963,
        elevation_m=40.0,
        timezone="America/Anchorage",
        temp_unit="F",
        aliases=("anchorage",),
        resolution_source="Weather Underground / PANC",
    ),

    # ── Original international cities ───────────────────────────────────
    "london": WeatherStation(
        city_name="London",
        station_id="EGLC",
        ghcnd_id="GHCND:UKE00105915",
        latitude=51.5053,
        longitude=0.0553,
        elevation_m=5.0,
        timezone="Europe/London",
        temp_unit="C",
        aliases=("london",),
        resolution_source="Weather Underground / EGLC",
        local_model="ukmo_seamless",
    ),
    "toronto": WeatherStation(
        city_name="Toronto",
        station_id="CYYZ",
        ghcnd_id="GHCND:CA006158733",
        latitude=43.6772,
        longitude=-79.6306,
        elevation_m=173.0,
        timezone="America/Toronto",
        temp_unit="C",
        aliases=("toronto",),
        resolution_source="Weather Underground / CYYZ",
        local_model="gem_seamless",
    ),
    "seoul": WeatherStation(
        city_name="Seoul",
        station_id="RKSS",
        ghcnd_id="GHCND:KSM00047108",
        latitude=37.5583,
        longitude=126.7906,
        elevation_m=18.0,
        timezone="Asia/Seoul",
        temp_unit="C",
        aliases=("seoul",),
        resolution_source="Weather Underground / RKSS",
        local_model="jma_seamless",
    ),
    "buenos_aires": WeatherStation(
        city_name="Buenos Aires",
        station_id="SAEZ",
        ghcnd_id="GHCND:AR000875850",
        latitude=-34.8222,
        longitude=-58.5358,
        elevation_m=20.0,
        timezone="America/Argentina/Buenos_Aires",
        temp_unit="C",
        aliases=("buenos aires",),
        resolution_source="Weather Underground / SAEZ",
    ),
    "wellington": WeatherStation(
        city_name="Wellington",
        station_id="NZWN",
        ghcnd_id="GHCND:NZM00093436",
        latitude=-41.3272,
        longitude=174.8053,
        elevation_m=7.0,
        timezone="Pacific/Auckland",
        temp_unit="C",
        aliases=("wellington",),
        resolution_source="Weather Underground / NZWN",
    ),
    "ankara": WeatherStation(
        city_name="Ankara",
        station_id="LTAC",
        ghcnd_id="GHCND:TUM00017130",
        latitude=40.1281,
        longitude=32.9951,
        elevation_m=953.0,
        timezone="Europe/Istanbul",
        temp_unit="C",
        aliases=("ankara",),
        resolution_source="Weather Underground / LTAC",
    ),

    # ── Expanded international cities ───────────────────────────────────
    "tokyo": WeatherStation(
        city_name="Tokyo",
        station_id="RJTT",
        ghcnd_id="GHCND:JA000047670",
        latitude=35.5533,
        longitude=139.7811,
        elevation_m=8.0,
        timezone="Asia/Tokyo",
        temp_unit="C",
        aliases=("tokyo",),
        resolution_source="Weather Underground / RJTT",
        local_model="jma_seamless",
    ),
    "sydney": WeatherStation(
        city_name="Sydney",
        station_id="YSSY",
        ghcnd_id="GHCND:ASN00066037",
        latitude=-33.9461,
        longitude=151.1772,
        elevation_m=6.0,
        timezone="Australia/Sydney",
        temp_unit="C",
        aliases=("sydney",),
        resolution_source="Weather Underground / YSSY",
    ),
    "melbourne": WeatherStation(
        city_name="Melbourne",
        station_id="YMML",
        ghcnd_id="GHCND:ASN00086282",
        latitude=-37.6655,
        longitude=144.8321,
        elevation_m=132.0,
        timezone="Australia/Melbourne",
        temp_unit="C",
        aliases=("melbourne",),
        resolution_source="Weather Underground / YMML",
    ),
    "paris": WeatherStation(
        city_name="Paris",
        station_id="LFPB",
        ghcnd_id="GHCND:FR000007149",
        latitude=48.9694,
        longitude=2.4414,
        elevation_m=68.0,
        timezone="Europe/Paris",
        temp_unit="C",
        aliases=("paris",),
        resolution_source="Weather Underground / LFPB",
        local_model="meteofrance_seamless",
    ),
    "berlin": WeatherStation(
        city_name="Berlin",
        station_id="EDDB",
        ghcnd_id="GHCND:GME00111406",
        latitude=52.3680,
        longitude=13.5022,
        elevation_m=37.0,
        timezone="Europe/Berlin",
        temp_unit="C",
        aliases=("berlin",),
        resolution_source="Weather Underground / EDDB",
        local_model="icon_d2",
    ),
    "dubai": WeatherStation(
        city_name="Dubai",
        station_id="OMDB",
        ghcnd_id="GHCND:AE000041217",
        latitude=25.2528,
        longitude=55.3644,
        elevation_m=34.0,
        timezone="Asia/Dubai",
        temp_unit="C",
        aliases=("dubai",),
        resolution_source="Weather Underground / OMDB",
    ),
    "mexico_city": WeatherStation(
        city_name="Mexico City",
        station_id="MMMX",
        ghcnd_id="GHCND:MX000076679",
        latitude=19.4363,
        longitude=-99.0721,
        elevation_m=2229.0,
        timezone="America/Mexico_City",
        temp_unit="C",
        aliases=("mexico city", "ciudad de mexico", "cdmx"),
        resolution_source="Weather Underground / MMMX",
    ),
    "sao_paulo": WeatherStation(
        city_name="São Paulo",
        station_id="SBGR",
        ghcnd_id="GHCND:BR003563",
        latitude=-23.4356,
        longitude=-46.4731,
        elevation_m=750.0,
        timezone="America/Sao_Paulo",
        temp_unit="C",
        aliases=("são paulo", "sao paulo", "sp"),
        resolution_source="Weather Underground / SBGR",
    ),
    "amsterdam": WeatherStation(
        city_name="Amsterdam",
        station_id="EHAM",
        ghcnd_id="GHCND:NL000006240",
        latitude=52.3105,
        longitude=4.7683,
        elevation_m=-4.0,
        timezone="Europe/Amsterdam",
        temp_unit="C",
        aliases=("amsterdam",),
        resolution_source="Weather Underground / EHAM",
        local_model="knmi_seamless",
    ),
    "mumbai": WeatherStation(
        city_name="Mumbai",
        station_id="VABB",
        ghcnd_id="GHCND:IN022021",
        latitude=19.0896,
        longitude=72.8656,
        elevation_m=14.0,
        timezone="Asia/Kolkata",
        temp_unit="C",
        aliases=("mumbai", "bombay"),
        resolution_source="Weather Underground / VABB",
    ),
    "vienna": WeatherStation(
        city_name="Vienna",
        station_id="LOWW",
        ghcnd_id="GHCND:AU000005901",
        latitude=48.1100,
        longitude=16.5697,
        elevation_m=183.0,
        timezone="Europe/Vienna",
        temp_unit="C",
        aliases=("vienna", "wien"),
        resolution_source="Weather Underground / LOWW",
        local_model="icon_d2",
    ),
    "stockholm": WeatherStation(
        city_name="Stockholm",
        station_id="ESSA",
        ghcnd_id="GHCND:SW000002498",
        latitude=59.6519,
        longitude=17.9186,
        elevation_m=61.0,
        timezone="Europe/Stockholm",
        temp_unit="C",
        aliases=("stockholm",),
        resolution_source="Weather Underground / ESSA",
        local_model="icon_d2",
    ),
    "oslo": WeatherStation(
        city_name="Oslo",
        station_id="ENGM",
        ghcnd_id="GHCND:NO000018700",
        latitude=60.1939,
        longitude=11.1004,
        elevation_m=202.0,
        timezone="Europe/Oslo",
        temp_unit="C",
        aliases=("oslo",),
        resolution_source="Weather Underground / ENGM",
        local_model="icon_d2",
    ),
    "copenhagen": WeatherStation(
        city_name="Copenhagen",
        station_id="EKCH",
        ghcnd_id="GHCND:DA000006180",
        latitude=55.6139,
        longitude=12.6608,
        elevation_m=5.0,
        timezone="Europe/Copenhagen",
        temp_unit="C",
        aliases=("copenhagen",),
        resolution_source="Weather Underground / EKCH",
        local_model="dmi_seamless",
    ),
    "warsaw": WeatherStation(
        city_name="Warsaw",
        station_id="EPWA",
        ghcnd_id="GHCND:PL000012375",
        latitude=52.1657,
        longitude=20.9672,
        elevation_m=110.0,
        timezone="Europe/Warsaw",
        temp_unit="C",
        aliases=("warsaw",),
        resolution_source="Weather Underground / EPWA",
        local_model="icon_d2",
    ),
    "prague": WeatherStation(
        city_name="Prague",
        station_id="LKPR",
        ghcnd_id="GHCND:EZ000011518",
        latitude=50.1008,
        longitude=14.2600,
        elevation_m=380.0,
        timezone="Europe/Prague",
        temp_unit="C",
        aliases=("prague",),
        resolution_source="Weather Underground / LKPR",
        local_model="icon_d2",
    ),
    "zurich": WeatherStation(
        city_name="Zurich",
        station_id="LSZH",
        ghcnd_id="GHCND:SZ000006670",
        latitude=47.4647,
        longitude=8.5492,
        elevation_m=432.0,
        timezone="Europe/Zurich",
        temp_unit="C",
        aliases=("zurich", "zürich"),
        resolution_source="Weather Underground / LSZH",
        local_model="icon_d2",
    ),
    "brussels": WeatherStation(
        city_name="Brussels",
        station_id="EBBR",
        ghcnd_id="GHCND:BE000006447",
        latitude=50.9014,
        longitude=4.4844,
        elevation_m=58.0,
        timezone="Europe/Brussels",
        temp_unit="C",
        aliases=("brussels", "bruxelles"),
        resolution_source="Weather Underground / EBBR",
        local_model="icon_d2",
    ),
    "madrid": WeatherStation(
        city_name="Madrid",
        station_id="LEMD",
        ghcnd_id="GHCND:SP000008221",
        latitude=40.4936,
        longitude=-3.5669,
        elevation_m=610.0,
        timezone="Europe/Madrid",
        temp_unit="C",
        aliases=("madrid",),
        resolution_source="Weather Underground / LEMD",
        local_model="icon_d2",
    ),
    "rome": WeatherStation(
        city_name="Rome",
        station_id="LIRF",
        ghcnd_id="GHCND:IT000016232",
        latitude=41.8003,
        longitude=12.2389,
        elevation_m=48.0,
        timezone="Europe/Rome",
        temp_unit="C",
        aliases=("rome", "roma"),
        resolution_source="Weather Underground / LIRF",
        local_model="icon_d2",
    ),
    "singapore": WeatherStation(
        city_name="Singapore",
        station_id="WSSS",
        ghcnd_id="GHCND:SN000048698",
        latitude=1.3591,
        longitude=103.9894,
        elevation_m=14.0,
        timezone="Asia/Singapore",
        temp_unit="C",
        aliases=("singapore",),
        resolution_source="Weather Underground / WSSS",
    ),
    "hong_kong": WeatherStation(
        city_name="Hong Kong",
        station_id="VHHH",
        ghcnd_id="GHCND:HK000045005",
        latitude=22.3089,
        longitude=113.9150,
        elevation_m=9.0,
        timezone="Asia/Hong_Kong",
        temp_unit="C",
        aliases=("hong kong",),
        resolution_source="Weather Underground / VHHH",
    ),
    "bangkok": WeatherStation(
        city_name="Bangkok",
        station_id="VTBS",
        ghcnd_id="GHCND:TH000048455",
        latitude=13.6900,
        longitude=100.7506,
        elevation_m=2.0,
        timezone="Asia/Bangkok",
        temp_unit="C",
        aliases=("bangkok",),
        resolution_source="Weather Underground / VTBS",
    ),
    "taipei": WeatherStation(
        city_name="Taipei",
        station_id="RCTP",
        ghcnd_id="GHCND:TW000046696",
        latitude=25.0631,
        longitude=121.5531,
        elevation_m=33.0,
        timezone="Asia/Taipei",
        temp_unit="C",
        aliases=("taipei",),
        resolution_source="Weather Underground / RCTP",
    ),
    "vancouver": WeatherStation(
        city_name="Vancouver",
        station_id="CYVR",
        ghcnd_id="GHCND:CA001108395",
        latitude=49.1967,
        longitude=-123.1839,
        elevation_m=5.0,
        timezone="America/Vancouver",
        temp_unit="C",
        aliases=("vancouver",),
        resolution_source="Weather Underground / CYVR",
        local_model="gem_seamless",
    ),
    "montreal": WeatherStation(
        city_name="Montreal",
        station_id="CYUL",
        ghcnd_id="GHCND:CA006158733",
        latitude=45.4706,
        longitude=-73.7408,
        elevation_m=36.0,
        timezone="America/Montreal",
        temp_unit="C",
        aliases=("montreal", "montréal"),
        resolution_source="Weather Underground / CYUL",
        local_model="gem_seamless",
    ),
    "auckland": WeatherStation(
        city_name="Auckland",
        station_id="NZAA",
        ghcnd_id="GHCND:NZ000093110",
        latitude=-37.0082,
        longitude=174.7917,
        elevation_m=6.0,
        timezone="Pacific/Auckland",
        temp_unit="C",
        aliases=("auckland",),
        resolution_source="Weather Underground / NZAA",
    ),
    "johannesburg": WeatherStation(
        city_name="Johannesburg",
        station_id="FAOR",
        ghcnd_id="GHCND:SF000683690",
        latitude=-26.1392,
        longitude=28.2461,
        elevation_m=1694.0,
        timezone="Africa/Johannesburg",
        temp_unit="C",
        aliases=("johannesburg", "joburg", "jo'burg"),
        resolution_source="Weather Underground / FAOR",
    ),
    "cairo": WeatherStation(
        city_name="Cairo",
        station_id="HECA",
        ghcnd_id="GHCND:EG000062306",
        latitude=30.1219,
        longitude=31.4056,
        elevation_m=75.0,
        timezone="Africa/Cairo",
        temp_unit="C",
        aliases=("cairo",),
        resolution_source="Weather Underground / HECA",
    ),
    "istanbul": WeatherStation(
        city_name="Istanbul",
        station_id="LTBA",
        ghcnd_id="GHCND:TUM00017060",
        latitude=40.9769,
        longitude=28.8146,
        elevation_m=39.0,
        timezone="Europe/Istanbul",
        temp_unit="C",
        aliases=("istanbul",),
        resolution_source="Weather Underground / LTBA",
    ),
    "athens": WeatherStation(
        city_name="Athens",
        station_id="LGAV",
        ghcnd_id="GHCND:GR000016716",
        latitude=37.9367,
        longitude=23.9444,
        elevation_m=94.0,
        timezone="Europe/Athens",
        temp_unit="C",
        aliases=("athens", "athina"),
        resolution_source="Weather Underground / LGAV",
    ),
    "lisbon": WeatherStation(
        city_name="Lisbon",
        station_id="LPPT",
        ghcnd_id="GHCND:PO000008535",
        latitude=38.7756,
        longitude=-9.1354,
        elevation_m=114.0,
        timezone="Europe/Lisbon",
        temp_unit="C",
        aliases=("lisbon", "lisboa"),
        resolution_source="Weather Underground / LPPT",
    ),
    "dublin": WeatherStation(
        city_name="Dublin",
        station_id="EIDW",
        ghcnd_id="GHCND:IE000003969",
        latitude=53.4213,
        longitude=-6.2700,
        elevation_m=68.0,
        timezone="Europe/Dublin",
        temp_unit="C",
        aliases=("dublin",),
        resolution_source="Weather Underground / EIDW",
        local_model="ukmo_seamless",
    ),
    "helsinki": WeatherStation(
        city_name="Helsinki",
        station_id="EFHK",
        ghcnd_id="GHCND:FI000028450",
        latitude=60.3172,
        longitude=24.9633,
        elevation_m=56.0,
        timezone="Europe/Helsinki",
        temp_unit="C",
        aliases=("helsinki",),
        resolution_source="Weather Underground / EFHK",
        local_model="icon_d2",
    ),
    "beijing": WeatherStation(
        city_name="Beijing",
        station_id="ZBAA",
        ghcnd_id="GHCND:CH000054511",
        latitude=40.0799,
        longitude=116.5853,
        elevation_m=35.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("beijing",),
        resolution_source="Weather Underground / ZBAA",
    ),
    "shanghai": WeatherStation(
        city_name="Shanghai",
        station_id="ZSPD",
        ghcnd_id="GHCND:CH000058362",
        latitude=31.1443,
        longitude=121.8083,
        elevation_m=4.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("shanghai",),
        resolution_source="Weather Underground / ZSPD",
    ),
    "chengdu": WeatherStation(
        city_name="Chengdu",
        station_id="ZUUU",
        ghcnd_id="GHCND:CH000056294",
        latitude=30.5728,
        longitude=104.0668,
        elevation_m=506.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("chengdu",),
        resolution_source="Weather Underground / ZUUU",
    ),
    "chongqing": WeatherStation(
        city_name="Chongqing",
        station_id="ZUCK",
        ghcnd_id="GHCND:CH000057516",
        latitude=29.5630,
        longitude=106.6516,
        elevation_m=397.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("chongqing",),
        resolution_source="Weather Underground / ZUCK",
    ),
    "shenzhen": WeatherStation(
        city_name="Shenzhen",
        station_id="ZGSZ",
        ghcnd_id="GHCND:CH000059493",
        latitude=22.6328,
        longitude=113.8106,
        elevation_m=5.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("shenzhen",),
        resolution_source="Weather Underground / ZGSZ",
    ),
    "wuhan": WeatherStation(
        city_name="Wuhan",
        station_id="ZHHH",
        ghcnd_id="GHCND:CH000057494",
        latitude=30.7931,
        longitude=114.2055,
        elevation_m=22.0,
        timezone="Asia/Shanghai",
        temp_unit="C",
        aliases=("wuhan",),
        resolution_source="Weather Underground / ZHHH",
    ),
    "delhi": WeatherStation(
        city_name="Delhi",
        station_id="VIDP",
        ghcnd_id="GHCND:IN022022",
        latitude=28.5665,
        longitude=77.1031,
        elevation_m=237.0,
        timezone="Asia/Kolkata",
        temp_unit="C",
        aliases=("delhi", "new delhi"),
        resolution_source="Weather Underground / VIDP",
    ),
    "kuala_lumpur": WeatherStation(
        city_name="Kuala Lumpur",
        station_id="WMKK",
        ghcnd_id="GHCND:MY000048650",
        latitude=2.7456,
        longitude=101.7072,
        elevation_m=21.0,
        timezone="Asia/Kuala_Lumpur",
        temp_unit="C",
        aliases=("kuala lumpur", "kl"),
        resolution_source="Weather Underground / WMKK",
    ),
    "jakarta": WeatherStation(
        city_name="Jakarta",
        station_id="WIII",
        ghcnd_id="GHCND:ID000096749",
        latitude=-6.1256,
        longitude=106.6558,
        elevation_m=8.0,
        timezone="Asia/Jakarta",
        temp_unit="C",
        aliases=("jakarta",),
        resolution_source="Weather Underground / WIII",
    ),
    "nairobi": WeatherStation(
        city_name="Nairobi",
        station_id="HKJK",
        ghcnd_id="GHCND:KE000063820",
        latitude=-1.3192,
        longitude=36.9275,
        elevation_m=1624.0,
        timezone="Africa/Nairobi",
        temp_unit="C",
        aliases=("nairobi",),
        resolution_source="Weather Underground / HKJK",
    ),
    "lucknow": WeatherStation(
        city_name="Lucknow",
        station_id="VILK",
        ghcnd_id="GHCND:IN012440100",
        latitude=26.7606,
        longitude=80.8893,
        elevation_m=128.0,
        timezone="Asia/Kolkata",
        temp_unit="C",
        aliases=("lucknow",),
        resolution_source="Weather Underground / VILK",
    ),
    "munich": WeatherStation(
        city_name="Munich",
        station_id="EDDM",
        ghcnd_id="GHCND:GME00111445",
        latitude=48.3537,
        longitude=11.7860,
        elevation_m=453.0,
        timezone="Europe/Berlin",
        temp_unit="C",
        aliases=("munich", "münchen"),
        resolution_source="Weather Underground / EDDM",
        local_model="icon_d2",
    ),
    # S101b: Added — discovered via city discovery logging (Polymarket active)
    "milan": WeatherStation(
        city_name="Milan",
        station_id="LIML",
        ghcnd_id="GHCND:IT000160590",
        latitude=45.4454,
        longitude=9.2743,
        elevation_m=103.0,
        timezone="Europe/Rome",
        temp_unit="C",
        aliases=("milan", "milano"),
        resolution_source="Weather Underground / LIML (Linate)",
        local_model="meteofrance_seamless",
    ),

    "tel_aviv": WeatherStation(
        city_name="Tel Aviv",
        station_id="LLBG",
        ghcnd_id="GHCND:IS000006240",
        latitude=32.0114,
        longitude=34.8867,
        elevation_m=41.0,
        timezone="Asia/Jerusalem",
        temp_unit="C",
        aliases=("tel aviv", "telaviv", "tel-aviv"),
        resolution_source="Weather Underground / LLBG",
    ),

    # S142: Moscow — previously unmatched, logged every scan
    "moscow": WeatherStation(
        city_name="Moscow",
        station_id="UUWW",
        ghcnd_id="GHCND:RSM00027612",
        latitude=55.9726,
        longitude=37.4146,
        elevation_m=190.0,
        timezone="Europe/Moscow",
        temp_unit="C",
        aliases=("moscow",),
        resolution_source="Weather Underground / UUWW (Sheremetyevo)",
    ),
}

# Build alias → station lookup (pre-computed at import time)
_ALIAS_MAP: Dict[str, WeatherStation] = {}
for _station in STATION_REGISTRY.values():
    for _alias in _station.aliases:
        _ALIAS_MAP[_alias] = _station

# All US stations (temp_unit == "F") — used for cross-city regime detection
US_CITY_NAMES: frozenset = frozenset(
    s.city_name for s in STATION_REGISTRY.values() if s.temp_unit == "F"
)

# Runtime-addable registry populated by city_autodiscovery.try_auto_register()
# and pre-loaded from dynamic_stations DB on bot startup.
# Keys: lowercase station_key (e.g. "riyadh", "cape_town").
_DYNAMIC_REGISTRY: Dict[str, WeatherStation] = {}


def register_dynamic_station(
    station_key: str,
    city_name: str,
    latitude: float,
    longitude: float,
    timezone: str,
    temp_unit: str,
    aliases: List[str],
) -> WeatherStation:
    """Add a geocoded city to the in-process dynamic registry.

    Called by city_autodiscovery after a successful DB insert, and by
    load_dynamic_stations_from_db on bot startup. Dynamic stations use
    the station_key as their station_id (no ICAO code available).
    """
    station = WeatherStation(
        city_name=city_name,
        station_id=station_key,      # no ICAO — used as a unique key only
        ghcnd_id="",                 # no GHCND for auto-discovered cities
        latitude=latitude,
        longitude=longitude,
        elevation_m=0.0,             # elevation ignored by Open-Meteo lat/lon query
        timezone=timezone,
        temp_unit=temp_unit,
        aliases=tuple(a.lower() for a in aliases),
        resolution_source="dynamic",
    )
    _DYNAMIC_REGISTRY[station_key] = station
    for alias in station.aliases:
        _DYNAMIC_REGISTRY[alias] = station
    return station


async def load_dynamic_stations_from_db(db: object) -> int:
    """Pre-load all rows from dynamic_stations into _DYNAMIC_REGISTRY.

    Call once during bot startup so that previously auto-discovered cities
    are available immediately without waiting for the first new detection.
    Returns the count of stations loaded.
    """
    if db is None or not hasattr(db, "session_factory") or db.session_factory is None:
        return 0
    try:
        from sqlalchemy import text as _sa_text
        async with db.get_session() as sess:
            rows = (await sess.execute(
                _sa_text(
                    "SELECT station_key, city_name, latitude, longitude, "
                    "timezone, temp_unit, aliases FROM dynamic_stations"
                )
            )).fetchall()
        for row in rows:
            register_dynamic_station(
                station_key=row[0],
                city_name=row[1],
                latitude=float(row[2]),
                longitude=float(row[3]),
                timezone=row[4],
                temp_unit=row[5],
                aliases=list(row[6]) if row[6] else [row[0]],
            )
        if rows:
            logger.info(
                "dynamic_stations_loaded",
                count=len(rows),
                cities=[r[1] for r in rows],
            )
        return len(rows)
    except Exception as exc:
        logger.warning("dynamic_stations_load_failed", error=str(exc))
        return 0


def lookup_station(city_text: str) -> Optional[WeatherStation]:
    """Match city text (from a market question) to a station.

    Tries exact alias match first, then word-boundary substring search against
    the static registry, then the dynamic registry (auto-discovered cities).
    Returns None if no match found.

    M8: Substring matching now requires word boundaries to avoid
    false positives (e.g., "San Francisco Bay Area" matching the wrong station).
    """
    if not city_text:
        return None
    text = city_text.strip().lower()
    # Exact alias match (static)
    if text in _ALIAS_MAP:
        return _ALIAS_MAP[text]
    # M8: Word-boundary substring match (longest alias first, static)
    import re
    for alias in sorted(_ALIAS_MAP, key=len, reverse=True):
        # Require alias to be at a word boundary in the text
        pattern = r"(?:^|\b)" + re.escape(alias) + r"(?:\b|$)"
        if re.search(pattern, text):
            logger.debug(
                "station_substring_match",
                input=city_text,
                matched_alias=alias,
                station=_ALIAS_MAP[alias].station_id,
            )
            return _ALIAS_MAP[alias]
    # Dynamic registry fallback (runtime-added auto-discovered cities)
    if text in _DYNAMIC_REGISTRY:
        return _DYNAMIC_REGISTRY[text]
    for alias in sorted(_DYNAMIC_REGISTRY, key=len, reverse=True):
        pattern = r"(?:^|\b)" + re.escape(alias) + r"(?:\b|$)"
        if re.search(pattern, text):
            logger.debug(
                "dynamic_station_match",
                input=city_text,
                matched_alias=alias,
                station=_DYNAMIC_REGISTRY[alias].station_id,
            )
            return _DYNAMIC_REGISTRY[alias]
    return None


class StationHealthMonitor:
    """Monitor weather station observation health.

    Checks that the resolution station is reporting recent observations.
    Halts trading for a station if data is stale or anomalous.
    """

    def __init__(self, stale_threshold_minutes: float = 180.0):
        self._stale_threshold = stale_threshold_minutes * 60.0
        self._health_cache: Dict[str, tuple] = {}  # station_id -> (is_healthy, mono_expiry)
        self._cache_ttl = 600.0  # 10 min
        self._probe_429_until: float = 0.0  # S151: global Open-Meteo probe cooldown

    async def is_healthy(self, station: WeatherStation) -> bool:
        """Return True if station is reporting recent observations."""
        now = time.monotonic()
        cached = self._health_cache.get(station.station_id)
        if cached and now < cached[1]:
            return cached[0]

        healthy = await self._check_station(station)
        self._health_cache[station.station_id] = (healthy, now + self._cache_ttl)
        return healthy

    async def _check_station(self, station: WeatherStation) -> bool:
        """Query NWS (US) or Open-Meteo (international) to verify station liveness.

        International stations probe Open-Meteo for a 1-day forecast — if the API
        returns valid temperature data for the station's coordinates, it's healthy.
        """
        if station.temp_unit == "C":
            return await self._probe_openmeteo(station)

        url = f"https://api.weather.gov/stations/{station.station_id}/observations/latest"
        try:
            async with aiohttp.ClientSession() as sess:
                headers = {
                    "User-Agent": "PolymarketWeatherBot/1.0",
                    "Accept": "application/geo+json",
                }
                async with sess.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "station_health_check_failed",
                            station=station.station_id,
                            status=resp.status,
                        )
                        return True  # Fail open — don't block trading on API error
                    data = await resp.json()
            ts_str = data.get("properties", {}).get("timestamp")
            if not ts_str:
                return True
            obs_time = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            age_seconds = (datetime.now(_tz.utc) - obs_time).total_seconds()
            if age_seconds > self._stale_threshold:
                logger.warning(
                    "station_stale_observation",
                    station=station.station_id,
                    age_hours=round(age_seconds / 3600, 1),
                )
                return False
            return True
        except Exception as exc:
            logger.debug("station_health_error", station=station.station_id, error=str(exc))
            return True  # Fail open

    async def _probe_openmeteo(self, station: WeatherStation) -> bool:
        """Probe Open-Meteo for a 1-day forecast to verify international station health.

        Returns True if Open-Meteo returns non-null temperature data for the
        station's coordinates. Fails open (returns True) on any error.
        """
        # S151: global 429 cooldown — skip all probes if rate-limited
        if time.monotonic() < self._probe_429_until:
            return True  # Fail open during cooldown
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": station.latitude,
            "longitude": station.longitude,
            "daily": "temperature_2m_max",
            "forecast_days": 1,
            "timezone": "auto",
        }
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    url, params=params, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 429:
                        _cd = 300.0  # 5-min cooldown matching forecast_client pattern
                        self._probe_429_until = time.monotonic() + _cd
                        logger.warning("station_probe_429_cooldown_set", cooldown_s=int(_cd))
                        return True  # Fail open — rate limited
                    if resp.status != 200:
                        logger.warning(
                            "intl_station_probe_failed",
                            station=station.station_id,
                            status=resp.status,
                        )
                        return True  # Fail open — don't block trading on API error
                    data = await resp.json()
            daily = data.get("daily", {})
            maxes = daily.get("temperature_2m_max", [])
            if maxes and maxes[0] is not None:
                return True
            logger.warning(
                "intl_station_no_data",
                station=station.station_id,
                response_keys=list(data.keys()),
            )
            return True  # Fail open — data absence isn't definitive
        except Exception as exc:
            logger.debug(
                "intl_station_probe_error", station=station.station_id, error=str(exc)
            )
            return True  # Fail open
