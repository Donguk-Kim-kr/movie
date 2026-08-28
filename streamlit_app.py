'''
streamlit_app.py

- "카탈로그" 탭: KOBIS API를 실시간으로 직접 호출해서 보여줌
- "즐겨찾기 관리" 탭: 즐겨찾기 메모 수정 / 삭제
'''
import streamlit as st

st.set_page_config(
    page_title="영화 즐겨찾기",
    page_icon="🎬",
)

import requests
from contextlib import contextmanager

from fastapi import HTTPException

from database.db_connection import engine, SessionFactory
from database.orm import Base
from repositories.favorite_repository import FavoriteRepository
from repositories.movie_repository import MovieRepository
from schema.request import (
    FavoriteCreateRequest,
    FavoriteUpdateRequest,
    MovieCreateRequest,
)
from services.favorite_service import FavoriteService
from services.movie_service import MovieService


KOBIS_LIST_URL = (
    "https://www.kobis.or.kr/kobisopenapi/"
    "webservice/rest/movie/searchMovieList.json"
)

KOBIS_INFO_URL = (
    "https://www.kobis.or.kr/kobisopenapi/"
    "webservice/rest/movie/searchMovieInfo.json"
)

st.title("🎬 영화 카탈로그 & 즐겨찾기")

st.markdown(
    """
    <style>
    div[data-testid="stButton"] > button {
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def initialize_database():
    Base.metadata.create_all(bind=engine)
    return True


initialize_database()


@contextmanager
def open_services():
    with SessionFactory() as session:
        movie_repository = MovieRepository(session)
        favorite_repository = FavoriteRepository(session)

        try:
            yield (
                MovieService(movie_repository),
                FavoriteService(
                    favorite_repository,
                    movie_repository,
                ),
            )
        except Exception:
            session.rollback()
            raise


def get_kobis_key() -> str | None:
    return st.secrets.get("KOBIS_API_KEY")


RAW_PAGE_SIZE = 100
PAGE_SIZE = 12


def fetch_kobis_raw(
    api_key,
    keyword,
    director_nm,
    start_year,
    end_year,
    raw_page,
):
    params = {
        "key": api_key,
        "curPage": raw_page,
        "itemPerPage": RAW_PAGE_SIZE,
    }

    if keyword:
        params["movieNm"] = keyword

    if director_nm:
        params["directorNm"] = director_nm

    if start_year:
        params["openStartDt"] = start_year

    if end_year:
        params["openEndDt"] = end_year

    response = requests.get(
        KOBIS_LIST_URL,
        params=params,
        timeout=15,
    )

    response.raise_for_status()

    return response.json()["movieListResult"]


def fill_catalog_cache(
    api_key,
    keyword,
    director_nm,
    start_year,
    end_year,
    min_count,
):
    safety_limit = 50
    calls = 0

    while (
        len(st.session_state.catalog_cache) < min_count
        and not st.session_state.catalog_exhausted
        and calls < safety_limit
    ):
        raw = fetch_kobis_raw(
            api_key,
            keyword,
            director_nm,
            start_year,
            end_year,
            st.session_state.catalog_raw_page,
        )

        raw_movies = raw["movieList"]
        st.session_state.catalog_total = int(raw["totCnt"])

        filtered = [
            movie
            for movie in raw_movies
            if "성인물" not in (movie.get("genreAlt") or "")
        ]

        st.session_state.catalog_excluded += (
            len(raw_movies) - len(filtered)
        )

        st.session_state.catalog_cache.extend(filtered)

        if len(raw_movies) < RAW_PAGE_SIZE:
            st.session_state.catalog_exhausted = True

        st.session_state.catalog_raw_page += 1
        calls += 1


def fetch_kobis_detail(api_key, movie_cd):
    response = requests.get(
        KOBIS_INFO_URL,
        params={
            "key": api_key,
            "movieCd": movie_cd,
        },
        timeout=15,
    )

    response.raise_for_status()

    return response.json()["movieInfoResult"]["movieInfo"]


def add_to_favorites(api_key, movie_cd, movie_nm):
    try:
        info = fetch_kobis_detail(api_key, movie_cd)

        movie = MovieCreateRequest(
            movie_cd=movie_cd,
            movie_nm=movie_nm,
            movie_nm_en=info.get("movieNmEn") or None,
            open_dt=info.get("openDt") or None,
            show_tm=info.get("showTm") or None,
            genre="|".join(
                genre["genreNm"]
                for genre in info.get("genres", [])
            ) or None,
            nation="|".join(
                nation["nationNm"]
                for nation in info.get("nations", [])
            ) or None,
            directors=[
                director["peopleNm"]
                for director in info.get("directors", [])
            ],
            actors=[
                actor["peopleNm"]
                for actor in info.get("actors", [])
            ],
        )

        with open_services() as (
            movie_service,
            favorite_service,
        ):
            movie_service.create_movie_if_not_exists(movie)

            favorite_service.add_favorite(
                FavoriteCreateRequest(movie_cd=movie_cd)
            )

        st.success(f'"{movie_nm}" 즐겨찾기 추가됨')

    except HTTPException as error:
        st.warning(error.detail)

    except requests.RequestException:
        st.error("KOBIS 상세정보를 가져오지 못했습니다.")

    except Exception:
        st.error("즐겨찾기 저장 중 오류가 발생했습니다.")


tab_catalog, tab_manage = st.tabs(
    [
        "카탈로그 (KOBIS 실시간)",
        "즐겨찾기 관리",
    ]
)


with tab_catalog:
    api_key = get_kobis_key()

    if not api_key:
        st.error(
            "Secrets에 KOBIS_API_KEY가 설정되어 있지 않습니다."
        )
        st.stop()

    col1, col2 = st.columns(2)

    keyword = col1.text_input("영화명 검색")
    director_nm = col2.text_input("감독명 검색")

    (
        search_col,
        spacer_col,
        label_col,
        start_col,
        tilde_col,
        end_col,
    ) = st.columns([1, 3.4, 0.6, 0.6, 0.25, 0.6])

    with search_col:
        search_clicked = st.button("검색")

    with label_col:
        st.markdown(
            "<div style='padding-top:0.5rem; "
            "font-size:14px;'>개봉연도</div>",
            unsafe_allow_html=True,
        )

    start_year = start_col.text_input(
        "개봉연도 시작",
        placeholder="1919",
        label_visibility="collapsed",
    )

    with tilde_col:
        st.markdown(
            "<div style='text-align:center; "
            "padding-top:0.4rem;'>~</div>",
            unsafe_allow_html=True,
        )

    end_year = end_col.text_input(
        "개봉연도 종료",
        placeholder="2026",
        label_visibility="collapsed",
    )

    search_key = (
        keyword,
        director_nm,
        start_year,
        end_year,
    )

    if (
        "catalog_search_key" not in st.session_state
        or st.session_state.catalog_search_key != search_key
        or search_clicked
    ):
        st.session_state.catalog_search_key = search_key
        st.session_state.catalog_cache = []
        st.session_state.catalog_raw_page = 1
        st.session_state.catalog_exhausted = False
        st.session_state.catalog_total = 0
        st.session_state.catalog_excluded = 0
        st.session_state.catalog_display_page = 0

    display_page = st.session_state.catalog_display_page

    try:
        fill_catalog_cache(
            api_key,
            keyword,
            director_nm,
            start_year,
            end_year,
            (display_page + 2) * PAGE_SIZE,
        )

    except Exception:
        st.error("KOBIS 조회에 실패했습니다.")

    cache = st.session_state.catalog_cache

    movies = cache[
        display_page * PAGE_SIZE:
        (display_page + 1) * PAGE_SIZE
    ]

    has_next = len(cache) > (
        (display_page + 1) * PAGE_SIZE
    )

    st.caption(
        f"KOBIS 전체 검색결과 "
        f"{st.session_state.catalog_total}개 · "
        f"현재 페이지 {display_page + 1}"
    )

    for movie in movies:
        c1, c2 = st.columns([7, 1])

        c1.write(
            f'**{movie["movieNm"]}** · '
            f'{movie.get("openDt") or "-"} · '
            f'{movie.get("genreAlt") or "-"} · '
            f'{movie.get("nationAlt") or "-"}'
        )

        if c2.button(
            "즐겨찾기",
            key=f'fav_{movie["movieCd"]}',
        ):
            add_to_favorites(
                api_key,
                movie["movieCd"],
                movie["movieNm"],
            )

    _, nav1, nav2, _ = st.columns([3, 1, 1, 3])

    if nav1.button(
        "◀ 이전",
        disabled=display_page <= 0,
    ):
        st.session_state.catalog_display_page -= 1
        st.rerun()

    if nav2.button(
        "다음 ▶",
        disabled=not has_next,
    ):
        st.session_state.catalog_display_page += 1
        st.rerun()


with tab_manage:
    try:
        with open_services() as (_, favorite_service):
            favorites = [
                favorite.model_dump()
                for favorite
                in favorite_service.get_favorites()
            ]

    except Exception:
        st.error("즐겨찾기 목록을 불러오지 못했습니다.")
        st.stop()

    search_fav = st.text_input(
        "즐겨찾기 내 검색",
        placeholder="영화명으로 검색",
    )

    if search_fav:
        favorites = [
            favorite
            for favorite in favorites
            if search_fav in favorite["movie_nm"]
        ]

    if not favorites:
        st.info(
            '즐겨찾기한 영화가 없습니다. '
            '"카탈로그" 탭에서 먼저 추가해보세요.'
        )

    for favorite in favorites:
        c1, c2, c3 = st.columns([4, 1, 1])

        c1.write(
            f'**{favorite["movie_nm"]}** · '
            f'메모: {favorite.get("memo") or "-"}'
        )

        if c2.button(
            "수정",
            key=f'edit_fav_{favorite["id"]}',
        ):
            st.session_state.editing_fav = favorite["id"]
            st.rerun()

        if c3.button(
            "삭제",
            key=f'del_fav_{favorite["id"]}',
        ):
            st.session_state.confirming_delete = favorite["id"]
            st.rerun()

        if (
            st.session_state.get("confirming_delete")
            == favorite["id"]
        ):
            st.warning(
                f'"{favorite["movie_nm"]}"을(를) '
                "정말 삭제하시겠습니까?"
            )

            confirm_col, cancel_col = st.columns(2)

            if confirm_col.button(
                "네, 삭제합니다",
                key=f'confirm_del_{favorite["id"]}',
            ):
                try:
                    with open_services() as (
                        _,
                        favorite_service,
                    ):
                        favorite_service.delete_favorite(
                            favorite["id"]
                        )

                except HTTPException as error:
                    st.warning(error.detail)

                except Exception:
                    st.error(
                        "즐겨찾기 삭제 중 오류가 발생했습니다."
                    )

                else:
                    st.session_state.confirming_delete = None
                    st.success("즐겨찾기 삭제됨")
                    st.rerun()

            if cancel_col.button(
                "취소",
                key=f'cancel_del_{favorite["id"]}',
            ):
                st.session_state.confirming_delete = None
                st.rerun()

        if (
            st.session_state.get("editing_fav")
            == favorite["id"]
        ):
            with st.form(
                f'edit_fav_form_{favorite["id"]}'
            ):
                new_memo = st.text_input(
                    "메모",
                    favorite.get("memo") or "",
                    max_chars=200,
                )

                save = st.form_submit_button("저장")

            if save:
                try:
                    with open_services() as (
                        _,
                        favorite_service,
                    ):
                        favorite_service.update_favorite(
                            favorite["id"],
                            FavoriteUpdateRequest(
                                memo=new_memo
                            ),
                        )

                except HTTPException as error:
                    st.warning(error.detail)

                except Exception:
                    st.error(
                        "메모 수정 중 오류가 발생했습니다."
                    )

                else:
                    st.session_state.editing_fav = None
                    st.success("수정됨")
                    st.rerun()
