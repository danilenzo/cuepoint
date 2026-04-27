import copy
from typing import Any

_EVENT_LISTINGS_TEMPLATE: dict[str, Any] = {
    "operationName": "GET_EVENT_LISTINGS",
    "variables": {
        "filters": {
            "areas": {"eq": "__AREAS__"},
            "listingDate": {
                "gte": "__LISTING_DATE_GTE__",
                "lte": "__LISTING_DATE_LTE__",
            },
        },
        "filterOptions": {"genre": True},
        "pageSize": 20,
        "page": 1,
    },
    "query": (
        "query GET_EVENT_LISTINGS($filters: FilterInputDtoInput, $filterOptions: "
        "FilterOptionsInputDtoInput, $page: Int, $pageSize: Int) {eventListings("
        "filters: $filters, filterOptions: $filterOptions, pageSize: $pageSize, "
        "page: $page) {data {id listingDate event {...eventListingsFields artists "
        "{id name __typename} __typename} __typename} filterOptions {genre {label "
        "value __typename} __typename} totalResults __typename}}"
        "fragment eventListingsFields on Event {id date startTime endTime title "
        "contentUrl flyerFront isTicketed attending queueItEnabled newEventForm "
        "promoters {id name contentUrl live hasTicketAccess __typename} tickets("
        "queryType: AVAILABLE) {id title validType priceRetail onSaleFrom __typename} "
        "genres {id name slug __typename} images {id filename alt type crop __typename} "
        "pick {id blurb __typename} venue {id name contentUrl live __typename} __typename}"
    ),
}


def get_event_listings_payload(areas: int, listing_date_gte: str, listing_date_lte: str) -> dict[str, Any]:
    payload = copy.deepcopy(_EVENT_LISTINGS_TEMPLATE)
    payload["variables"]["filters"]["areas"]["eq"] = areas
    payload["variables"]["filters"]["listingDate"]["gte"] = listing_date_gte
    payload["variables"]["filters"]["listingDate"]["lte"] = listing_date_lte
    return payload


def get_artist_payload(slug: str) -> dict[str, Any]:
    return {
        "operationName": "GET_ARTIST_BY_SLUG",
        "variables": {
            "filters": {"slug": {"eq": slug}},
            "filterOptions": {"genre": True},
            "pageSize": 20,
            "page": 1,
            "slug": slug,
        },
        "query": "query GET_ARTIST_BY_SLUG($slug: String!) {\n  artist(slug: $slug) {\n    id\n    name\n    followerCount\n    firstName\n    lastName\n    aliases\n    isFollowing\n    coverImage\n    contentUrl\n    facebook\n    soundcloud\n    instagram\n    twitter\n    bandcamp\n    discogs\n    website\n    urlSafeName\n    pronouns\n    country {\n      id\n      name\n      urlCode\n      __typename\n    }\n    residentCountry {\n      id\n      name\n      urlCode\n      __typename\n    }\n    news(limit: 1) {\n      id\n      __typename\n    }\n    reviews(limit: 1, type: ALLMUSIC) {\n      id\n      __typename\n    }\n    ...biographyFields\n    __typename\n  }\n}\n\nfragment biographyFields on Artist {\n  id\n  name\n  contentUrl\n  image\n  biography {\n    id\n    blurb\n    content\n    discography\n    __typename\n  }\n  __typename\n}\n",
    }


def get_artist_payload_by_id(id: str | int) -> dict[str, Any]:
    return {
        "operationName": "GET_ARTIST_BY_ID",
        "variables": {
            "filters": {"id": {"eq": id}},
            "filterOptions": {"genre": True},
            "pageSize": 20,
            "page": 1,
            "id": id,
        },
        "query": "query GET_ARTIST_BY_ID($id: ID!) {\n  artist(id: $id) {\n    id\n    name\n    followerCount\n    firstName\n    lastName\n    aliases\n    isFollowing\n    coverImage\n    contentUrl\n    facebook\n    soundcloud\n    instagram\n    twitter\n    bandcamp\n    discogs\n    website\n    urlSafeName\n    pronouns\n    country {\n      id\n      name\n      urlCode\n      __typename\n    }\n    residentCountry {\n      id\n      name\n      urlCode\n      __typename\n    }\n    news(limit: 1) {\n      id\n      __typename\n    }\n    reviews(limit: 1, type: ALLMUSIC) {\n      id\n      __typename\n    }\n    ...biographyFields\n    __typename\n  }\n}\n\nfragment biographyFields on Artist {\n  id\n  name\n  contentUrl\n  image\n  biography {\n    id\n    blurb\n    content\n    discography\n    __typename\n  }\n  __typename\n}\n",
    }


def get_promoter_events_archive_payload_by_id(id: str | int) -> dict[str, Any]:
    return {
        "operationName": "GET_PROMOTER_EVENTS_ARCHIVE",
        "variables": {
            "filters": {"id": {"eq": id}},
            "filterOptions": {"genre": True},
            "pageSize": 20,
            "page": 1,
            "id": id,
        },
        "query": "query GET_PROMOTER_EVENTS_ARCHIVE($id: ID!) {\n  promoter(id: $id) {\n    id\n    events(limit: 5, type: PREVIOUS) {\n      id\n      title\n      interestedCount\n      isSaved\n      isInterested\n      date\n      contentUrl\n      flyerFront\n      newEventForm\n      images {\n        id\n        filename\n        alt\n        type\n        crop\n        __typename\n      }\n      pick {\n        id\n        blurb\n        __typename\n      }\n      artists {\n        id\n        name\n        __typename\n      }\n      venue {\n        id\n        name\n        contentUrl\n        live\n        area {\n          id\n          name\n          urlName\n          country {\n            id\n            name\n            urlCode\n            __typename\n          }\n          __typename\n        }\n        __typename\n      }\n      __typename\n    }\n    __typename\n  }\n}\n",
    }


def get_event_detail_payload_by_id(id: str | int) -> dict[str, Any]:
    return {
        "operationName": "GET_EVENT_DETAIL",
        "variables": {
            "filters": {"id": {"eq": id}},
            "filterOptions": {"genre": True},
            "pageSize": 20,
            "page": 1,
            "id": id,
        },
        "query": "query GET_EVENT_DETAIL($id: ID!, $isAuthenticated: Boolean!, $canAccessPresale: Boolean!, $enableNewBrunchTicketing: Boolean! = false) {\n  event(id: $id) {\n    id\n    title\n    flyerFront\n    flyerBack\n    content\n    minimumAge\n    cost\n    contentUrl\n    embargoDate\n    date\n    time\n    startTime\n    endTime\n    interestedCount\n    lineup\n    isInterested\n    isSaved\n    isTicketed\n    isFestival\n    dateUpdated\n    resaleActive\n    newEventForm\n    datePosted\n    hasSecretVenue\n    live\n    canSubscribeToTicketNotifications\n    images {\n      id\n      filename\n      alt\n      type\n      crop\n      __typename\n    }\n    venue {\n      id\n      name\n      address\n      contentUrl\n      live\n      area {\n        id\n        name\n        urlName\n        country {\n          id\n          name\n          urlCode\n          isoCode\n          __typename\n        }\n        __typename\n      }\n      location {\n        latitude\n        longitude\n        __typename\n      }\n      __typename\n    }\n    promoters {\n      id\n      name\n      contentUrl\n      live\n      hasTicketAccess\n      tracking(types: [PAGEVIEW]) {\n        id\n        code\n        event\n        __typename\n      }\n      __typename\n    }\n    artists {\n      id\n      name\n      contentUrl\n      urlSafeName\n      __typename\n    }\n    pick {\n      id\n      blurb\n      author {\n        id\n        name\n        imageUrl\n        username\n        contributor\n        __typename\n      }\n      __typename\n    }\n    promotionalLinks {\n      title\n      url\n      __typename\n    }\n    tracking(types: [PAGEVIEW]) {\n      id\n      code\n      event\n      __typename\n    }\n    admin {\n      id\n      username\n      __typename\n    }\n    tickets(queryType: AVAILABLE) {\n      id\n      title\n      validType\n      onSaleFrom\n      priceRetail\n      isAddOn\n      currency {\n        id\n        code\n        __typename\n      }\n      __typename\n    }\n    standardTickets: tickets(queryType: AVAILABLE, ticketTierType: TICKETS) {\n      id\n      validType\n      __typename\n    }\n    userOrders @include(if: $isAuthenticated) {\n      id\n      rAOrderNumber\n      __typename\n    }\n    playerLinks {\n      id\n      sourceId\n      audioService {\n        id\n        name\n        __typename\n      }\n      __typename\n    }\n    childEvents {\n      id\n      date\n      isTicketed\n      ...brunchChildEventFragment @include(if: $enableNewBrunchTicketing)\n      __typename\n    }\n    genres {\n      id\n      name\n      slug\n      __typename\n    }\n    setTimes {\n      id\n      lineup\n      status\n      __typename\n    }\n    area {\n      ianaTimeZone\n      __typename\n    }\n    presaleStatus\n    isSignedUpToPresale @include(if: $canAccessPresale)\n    ticketingSystem\n    __typename\n  }\n}\n\nfragment brunchChildEventFragment on Event {\n  canSubscribeToTicketNotifications\n  promoters {\n    id\n    __typename\n  }\n  standardTickets: tickets(queryType: AVAILABLE, ticketTierType: TICKETS) {\n    id\n    validType\n    __typename\n  }\n  __typename\n}\n",
    }
