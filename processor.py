
# -*- coding: utf-8 -*-
"""
Модуль обработки геоданных для плагина ClipReproject.

Практическая работа №2.
Выбор территории, обрезка векторных слоёв,
перепроецирование в ГСК-2011 и сохранение результатов.
"""

import os
import tempfile

from qgis import processing
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeatureRequest,
    QgsProject,
    QgsProviderRegistry,
    QgsVectorLayer,
    QgsWkbTypes,
)


class Processor:
    """Выполняет геообработку независимо от интерфейса плагина."""

    def process(self, params):
        """
        Обработать входные слои.

        params — словарь с параметрами:
            osm_dir
            boundary_path
            field_name
            field_value
            epsg
            result_dir

        Возвращает словарь с результатами обработки.
        """

        osm_dir = str(params["osm_dir"]).strip()
        boundary_path = str(params["boundary_path"]).strip()
        field_name = str(params["field_name"]).strip()
        field_value = str(params["field_value"]).strip()
        epsg = str(params["epsg"]).strip()
        result_dir = str(params["result_dir"]).strip()

        # Проверка основных параметров
        if not os.path.isdir(osm_dir):
            raise ValueError(
                "Выберите существующую папку со слоями OSM."
            )

        if not os.path.isfile(boundary_path):
            raise ValueError(
                "Выберите файл административных границ."
            )

        if not os.path.isdir(result_dir):
            raise ValueError(
                "Выберите существующую папку результатов."
            )

        if not field_name or not field_value:
            raise ValueError(
                "Введите имя поля и значение атрибута."
            )

        # Проверка системы координат
        epsg = epsg.upper().removeprefix("EPSG:").strip()

        if not epsg.isdigit():
            raise ValueError("Введите числовой EPSG-код.")

        target_crs = QgsCoordinateReferenceSystem(
            "EPSG:" + epsg
        )

        if not target_crs.isValid():
            raise ValueError(
                "Система координат с таким EPSG не найдена."
            )

        crs_name = target_crs.description().upper()

        if target_crs.isGeographic() or not (
            "GSK-2011" in crs_name
            or "ГСК-2011" in crs_name
        ):
            raise ValueError(
                "Для задания выберите проекционную "
                "систему ГСК-2011."
            )

        # 1. Загрузка административного слоя
        boundary = self.load_boundary(boundary_path)

        # 2. Выбор территории по атрибуту
        mask = self.select_territory(
            boundary,
            field_name,
            field_value
        )

        # 3. Поиск входных векторных слоёв
        layers = self.load_input_layers(
            osm_dir,
            boundary_path,
            boundary
        )

        # 4. Обрезка, перепроецирование, сохранение
        output_dir = tempfile.mkdtemp(
            prefix="result_",
            dir=result_dir
        )

        saved_layers = []

        try:
            for index, layer in enumerate(layers, start=1):

                output_path = os.path.join(
                    output_dir,
                    f"layer_{index:03d}_gsk2011.gpkg"
                )

                result_layer = self.process_layer(
                    layer,
                    mask,
                    target_crs,
                    output_path
                )

                QgsProject.instance().addMapLayer(
                    result_layer
                )

                saved_layers.append(output_path)

        except Exception as error:
            raise RuntimeError(
                f"{error}\n\n"
                f"Обработка остановлена.\n"
                f"Успешно сохранено слоёв: "
                f"{len(saved_layers)}\n"
                f"Папка частичных результатов:\n"
                f"{output_dir}"
            ) from error

        return {
            "saved_count": len(saved_layers),
            "output_dir": output_dir,
            "crs_name": target_crs.description(),
            "files": saved_layers,
        }

    @staticmethod
    def load_boundary(boundary_path):
        """Загрузить административный полигональный слой."""

        boundary = QgsVectorLayer(
            boundary_path,
            "Граница территории",
            "ogr"
        )

        if not boundary.isValid():
            raise ValueError(
                "Не удалось открыть слой границ."
            )

        if boundary.geometryType() != QgsWkbTypes.PolygonGeometry:
            raise ValueError(
                "Граница должна быть полигональным слоем."
            )

        if not boundary.crs().isValid():
            raise ValueError(
                "У слоя границ не определена система координат."
            )

        return boundary

    @staticmethod
    def select_territory(boundary, field_name, field_value):
        """Выбрать территорию по значению атрибута."""

        if boundary.fields().indexFromName(field_name) == -1:
            raise ValueError(
                "В слое границ отсутствует поле: "
                + field_name
            )

        expression = (
            "to_string("
            + QgsExpression.quotedColumnRef(field_name)
            + ") = "
            + QgsExpression.quotedString(field_value)
        )

        boundary.selectByExpression(expression)

        if boundary.selectedFeatureCount() == 0:
            raise ValueError(
                "Территория не найдена. "
                "Проверьте поле и значение."
            )

        request = QgsFeatureRequest().setFilterFids(
            boundary.selectedFeatureIds()
        )

        mask = boundary.materialize(request)

        return mask

    @staticmethod
    def load_input_layers(osm_dir, boundary_path, boundary):
        """Найти SHP и векторные слои GeoPackage."""

        layers = []
        registry = QgsProviderRegistry.instance()

        for filename in sorted(os.listdir(osm_dir)):

            if not filename.lower().endswith(
                (".shp", ".gpkg")
            ):
                continue

            path = os.path.join(osm_dir, filename)

            if os.path.normcase(os.path.realpath(path)) == (
                os.path.normcase(
                    os.path.realpath(boundary_path)
                )
            ):
                continue

            details = registry.querySublayers(path)

            vector_details = [
                item for item in details
                if item.type() == Qgis.LayerType.Vector
            ]

            if not vector_details:
                raise ValueError(
                    "Не найдены векторные слои: "
                    + filename
                )

            for item in vector_details:

                layer = QgsVectorLayer(
                    item.uri(),
                    item.name(),
                    item.providerKey()
                )

                if not layer.isValid():
                    raise ValueError(
                        "Не удалось открыть: " + filename
                    )

                if layer.geometryType() not in (
                    QgsWkbTypes.PointGeometry,
                    QgsWkbTypes.LineGeometry,
                    QgsWkbTypes.PolygonGeometry,
                ):
                    continue

                if not layer.crs().isValid():
                    raise ValueError(
                        "Не определена СК: " + filename
                    )

                if layer.crs() != boundary.crs():
                    raise ValueError(
                        "Исходные системы координат "
                        "не совпадают.\n"
                        "Проверьте слой: "
                        + filename
                        + " / "
                        + item.name()
                    )

                layers.append(layer)

        if not layers:
            raise ValueError(
                "В папке OSM не найдены "
                "векторные SHP/GPKG-слои."
            )

        geometry_types = {
            layer.geometryType()
            for layer in layers
        }

        if not {
            QgsWkbTypes.LineGeometry,
            QgsWkbTypes.PolygonGeometry,
        }.issubset(geometry_types):
            raise ValueError(
                "В папке должны быть минимум "
                "один линейный и один "
                "полигональный слой SHP или GPKG."
            )

        return layers

    @staticmethod
    def process_layer(layer, mask, target_crs, output_path):
        """Обрезать, перепроецировать и сохранить слой."""

        # Обрезка
        clipped = processing.run(
            "native:clip",
            {
                "INPUT": layer,
                "OVERLAY": mask,
                "OUTPUT": "TEMPORARY_OUTPUT",
            }
        )["OUTPUT"]

        # Перепроецирование
        processing.run(
            "native:reprojectlayer",
            {
                "INPUT": clipped,
                "TARGET_CRS": target_crs,
                "OUTPUT": output_path,
            }
        )

        # Открытие сохранённого результата
        result_layer = QgsVectorLayer(
            output_path,
            layer.name() + "_ГСК2011",
            "ogr"
        )

        if not result_layer.isValid():
            raise RuntimeError(
                "Не удалось открыть результат: "
                + output_path
            )

        return result_layer
