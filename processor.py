# -*- coding: utf-8 -*-
"""
ClipReproject — модуль обработки геоданных.

Практическая работа №2.
Обработка ошибок, журналирование и прогресс выполнения.
"""

import os
import tempfile

from qgis import processing
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsExpression,
    QgsFeatureRequest,
    QgsMessageLog,
    QgsProcessingFeedback,
    QgsProject,
    QgsProviderRegistry,
    QgsVectorLayer,
    QgsWkbTypes,
)


class Processor:
    """Геообработка без зависимости от диалогового окна."""

    LOG_TAG = "ClipReproject"

    @classmethod
    def log(cls, message, level=Qgis.Info):
        """Записать сообщение в журнал QGIS."""
        QgsMessageLog.logMessage(message, cls.LOG_TAG, level)

    def process(self, params):
        """Выполнить выбор, обрезку и перепроецирование."""

        feedback = QgsProcessingFeedback()
        output_dir = None
        saved_count = 0

        try:
            self.log("Начало обработки данных OSM.")

            osm_dir = str(params["osm_dir"]).strip()
            boundary_path = str(params["boundary_path"]).strip()
            field_name = str(params["field_name"]).strip()
            field_value = str(params["field_value"]).strip()
            epsg = str(params["epsg"]).strip()
            result_dir = str(params["result_dir"]).strip()

            # Проверяем входные данные
            if not os.path.isdir(osm_dir):
                raise ValueError("Папка исходных слоёв не существует.")

            if not os.path.isfile(boundary_path):
                raise ValueError("Файл границ не существует.")

            if not os.path.isdir(result_dir):
                raise ValueError("Папка результатов не существует.")

            if not field_name or not field_value:
                raise ValueError("Не указаны поле или значение атрибута.")

            epsg = epsg.upper().removeprefix("EPSG:").strip()

            if not epsg.isdigit():
                raise ValueError("Неверный EPSG-код.")

            target_crs = QgsCoordinateReferenceSystem("EPSG:" + epsg)

            if not target_crs.isValid():
                raise ValueError("Целевая система координат не найдена.")

            crs_name = target_crs.description().upper()

            if target_crs.isGeographic() or not ("GSK-2011" in crs_name or "ГСК-2011" in crs_name):
                raise ValueError("Требуется проекционная система ГСК-2011.")

            # Загружаем границы
            feedback.setProgressText("Загрузка административных границ")

            boundary = self.load_boundary(boundary_path)

            # Выбираем территорию
            mask = self.select_territory(boundary, field_name, field_value)

            self.log(f"Выбрана территория: {field_name} = {field_value}")

            # Загружаем входные слои
            layers = self.load_input_layers(osm_dir, boundary_path, boundary)

            total = len(layers)

            self.log(f"Найдено входных слоёв: {total}")

            # Создаём папку результатов
            output_dir = tempfile.mkdtemp(prefix="result_", dir=result_dir)

            # Обрабатываем слои
            for index, layer in enumerate(layers, start=1):
                progress_before = int((index - 1) / total * 100)

                feedback.setProgress(progress_before)

                message = f"Обработка {index}/{total}: {layer.name()}"

                feedback.setProgressText(message)
                self.log(f"{message} — {progress_before}%")

                output_path = os.path.join(output_dir, f"layer_{index:03d}_gsk2011.gpkg")

                result_layer = self.process_layer(layer, mask, target_crs, output_path, feedback)

                QgsProject.instance().addMapLayer(result_layer)

                saved_count += 1

                progress = int(index / total * 100)
                feedback.setProgress(progress)

                self.log(f"Слой {index}/{total} сохранён. Прогресс: {progress}%")

            feedback.setProgress(100)
            feedback.setProgressText("Обработка успешно завершена")

            self.log(f"Обработка завершена. Сохранено слоёв: {saved_count}")

            return {
                "saved_count": saved_count,
                "output_dir": output_dir,
                "crs_name": target_crs.description(),
            }

        except Exception as error:
            message = f"Ошибка обработки: {error}\nУспешно сохранено слоёв: {saved_count}"

            if output_dir:
                message += f"\nПапка частичных результатов:\n{output_dir}"

            self.log(message, Qgis.Critical)

            feedback.reportError(message, fatalError=True)

            raise RuntimeError(message) from error

    @staticmethod
    def load_boundary(boundary_path):
        """Загрузить административные границы."""

        layer = QgsVectorLayer(boundary_path, "Граница территории", "ogr")

        if not layer.isValid():
            raise ValueError("Не удалось открыть административный слой.")

        if layer.geometryType() != QgsWkbTypes.PolygonGeometry:
            raise ValueError("Административный слой должен быть полигональным.")

        if not layer.crs().isValid():
            raise ValueError("Не определена система координат границ.")

        return layer

    @staticmethod
    def select_territory(boundary, field_name, field_value):
        """Найти территорию по значению атрибута."""

        if boundary.fields().indexFromName(field_name) == -1:
            raise ValueError("Поле не найдено: " + field_name)

        expression = (
            "to_string("
            + QgsExpression.quotedColumnRef(field_name)
            + ") = "
            + QgsExpression.quotedString(field_value)
        )

        boundary.selectByExpression(expression)

        if boundary.selectedFeatureCount() == 0:
            raise ValueError("Территория с указанным атрибутом не найдена.")

        request = QgsFeatureRequest().setFilterFids(boundary.selectedFeatureIds())

        return boundary.materialize(request)

    @staticmethod
    def load_input_layers(osm_dir, boundary_path, boundary):
        """Загрузить все входные SHP и слои GPKG."""

        layers = []
        registry = QgsProviderRegistry.instance()

        for filename in sorted(os.listdir(osm_dir)):
            if not filename.lower().endswith((".shp", ".gpkg")):
                continue

            path = os.path.join(osm_dir, filename)

            if os.path.normcase(os.path.realpath(path)) == (
                os.path.normcase(os.path.realpath(boundary_path))
            ):
                continue

            details = registry.querySublayers(path)

            vector_details = [item for item in details if item.type() == Qgis.LayerType.Vector]

            if not vector_details:
                raise ValueError("Векторные слои не найдены: " + filename)

            for item in vector_details:
                layer = QgsVectorLayer(item.uri(), item.name(), item.providerKey())

                if not layer.isValid():
                    raise ValueError("Не удалось открыть слой: " + item.name())

                if layer.geometryType() not in (
                    QgsWkbTypes.PointGeometry,
                    QgsWkbTypes.LineGeometry,
                    QgsWkbTypes.PolygonGeometry,
                ):
                    continue

                if not layer.crs().isValid():
                    raise ValueError("Не определена СК слоя: " + item.name())

                if layer.crs() != boundary.crs():
                    raise ValueError("Системы координат не совпадают: " + item.name())

                layers.append(layer)

        if not layers:
            raise ValueError("В папке нет подходящих векторных слоёв.")

        geometry_types = {layer.geometryType() for layer in layers}

        if not {
            QgsWkbTypes.LineGeometry,
            QgsWkbTypes.PolygonGeometry,
        }.issubset(geometry_types):
            raise ValueError("Нужен минимум один линейный и один полигональный слой.")

        return layers

    @staticmethod
    def process_layer(layer, mask, target_crs, output_path, feedback):
        """Обрезать, перепроецировать и сохранить слой."""

        try:
            feedback.setProgressText("Обрезка: " + layer.name())

            clipped = processing.run(
                "native:clip",
                {
                    "INPUT": layer,
                    "OVERLAY": mask,
                    "OUTPUT": "TEMPORARY_OUTPUT",
                },
                feedback=feedback,
            )["OUTPUT"]

        except Exception as error:
            raise RuntimeError(f"Ошибка обрезки слоя {layer.name()}: {error}") from error

        try:
            feedback.setProgressText("Перепроецирование: " + layer.name())

            processing.run(
                "native:reprojectlayer",
                {
                    "INPUT": clipped,
                    "TARGET_CRS": target_crs,
                    "OUTPUT": output_path,
                },
                feedback=feedback,
            )

        except Exception as error:
            raise RuntimeError(f"Ошибка перепроецирования слоя {layer.name()}: {error}") from error

        result_layer = QgsVectorLayer(output_path, layer.name() + "_ГСК2011", "ogr")

        if not result_layer.isValid():
            raise RuntimeError("Не удалось открыть результат: " + output_path)

        return result_layer
